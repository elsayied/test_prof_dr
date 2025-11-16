import os
import time
from tqdm import tqdm
import json
from pprint import pprint
from pathlib import Path
import soundfile as sf
from kokoro import KPipeline, KModel
import torch
import io
import random
import requests
import ocrmypdf
import genanki
from ebooklib import epub
import numpy as np
import pdfplumber
import pymupdf  # PyMuPDF
import pymupdf 
import pytesseract
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from docx import Document
from google import genai
from config import get_api_key, get_setting
# ... other imports
from google.genai import types
import wave
from pydub import AudioSegment  # <-- ADD THIS
import tempfile                 # <-- ADD THIS (for a cleaner approach)
from config import get_api_key, get_setting
# ... rest of imports
import wave
import types
# --- Prompts ---
from prompts import (
    HIGH_YIELD_BASIC_CARDS_PROMPT,
    BASIC_CARDS_PROMPT,
    CLOZE_EXTRA_PROMPT,
    prompter
)

# --- Content Extraction ---

from extract_utils import (_get_chapters_by_heuristic, 
                           _get_chapters_from_toc, 
                           _get_ocr_text_for_page,
                           parse_toc_with_ai, 
                           extract_pdf_chapters)

# --- Content Extraction Functions ---
def extract_pdf_chapter_anki(path: str, ocr: bool = False, startegy = "chapters"):
    
    full_text = ""
    if startegy == "chapters":
        try:
            mupdf_doc = pymupdf.open(path) 
            doc_toc = mupdf_doc.get_toc()
            mupdf_doc.close()
            if doc_toc:
                doc_chapter_pages = [page for _, _, page in doc_toc]
                for i in range(
                       len(doc_chapter_pages) 
                    ):
                    full_text +=extract_pdf_content(
                        path, ocr, 
                        start_range=doc_chapter_pages[i],
                        end_range=doc_chapter_pages[i+1],
                    ) 
        except Exception as e:
            print(f"error occuer {e}")
            raise
    elif startegy == "full_document":
        full_text += extract_pdf_content(path, ocr)
    else: 
        print(f"{startegy = } is not implemented yet" )
def extract_pdf_content(path, ocr=False, start_range: int  = 0, end_range: int = -1,) -> str: # endrange =-1 means to the end

    """Extracts text, tables, and optionally performs OCR on images from a PDF file."""

    text = ""

    with pdfplumber.open(path) as plum_doc:

        mupdf_doc = pymupdf.open(path) if (ocr or divide_by_chapter) else None
        # QUESTION should i even consider recourising here
        
        for i, page in enumerate(tqdm(plum_doc.pages, desc=f"Processing PDF: {os.path.basename(path)}")):
            # Skip pages outside the desired range.
            # Note: end_range is exclusive, so the range is [start_range, end_range).
            
            if i < start_range:
                continue
            if end_range != -1 and i >= end_range:
                continue

            page_text = page.extract_text() or ""
            # Extract tables from the page
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        page_text += "\t".join(str(cell) if cell is not None else "" for cell in row) + "\n"
                    page_text += "\n"
            
            # Perform OCR on images if enabled
            if ocr and mupdf_doc:
                pymupdf_page = mupdf_doc[i]
                image_list = pymupdf_page.get_images(full=True)
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = mupdf_doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    try:
                        image_pil = Image.open(io.BytesIO(image_bytes))
                        ocr_text = pytesseract.image_to_string(image_pil)
                        if ocr_text.strip():
                            page_text += f"\n[OCR from Image {img_index+1}]:\n{ocr_text}\n"
                    except Exception as e:
                        print(f"  Error OCR'ing embedded image {img_index+1}: {e}")
            text += page_text + "\n  ---"
        if mupdf_doc:
            mupdf_doc.close()
    return text



def extract_docx_content(file_path: str):

    """Extracts all text from a DOCX file."""

    text_content = ""

    try:
        doc = Document(file_path)
        for para in tqdm(doc.paragraphs, desc=f"Processing DOCX: {os.path.basename(file_path)}"):
            text_content += para.text + "\n"
    except Exception as e:
        print(f"Error processing DOCX file '{file_path}': {e}")

    return text_content



def extract_pptx_content(file_path: str, ocr: bool = True, 
                         start_range: int = 0 , 
                         end_range: int = -1):

    """Extracts text from slides and optionally performs OCR on images in a PowerPoint file."""

    text_content = ""
    try:
        prs = Presentation(file_path)
        
        for i, slide in enumerate(tqdm(prs.slides, desc=f"Processing PPTX: {os.path.basename(file_path)}")):
            if i < start_range or (end_range != -1 and i >= end_range):
                continue
            slide_text = ""
            for shape in slide.shapes:
                # Extract text from shapes
                if hasattr(shape, "text"):
                    slide_text += shape.text + "\n"
                # Extract text from tables
                elif hasattr(shape, "has_table") and shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            slide_text += cell.text + "\t"
                        slide_text += "\n"
                # Perform OCR on images if enabled
                if ocr and shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        image_pil = Image.open(io.BytesIO(shape.image.blob))
                        ocr_text = pytesseract.image_to_string(image_pil)
                        if ocr_text.strip():
                            slide_text += f"\n[OCR from Image]:\n{ocr_text}\n"
                    except Exception as e:
                        print(f"  Error OCR'ing image on slide {i+1}: {e}")
            text_content += f"--- Slide {i+1} ---\n{slide_text}\n"
    except Exception as e:
        print(f"Error processing PowerPoint file '{file_path}': {e}")
    return text_content



from google.genai import types

from youtube_transcript_api import YouTubeTranscriptApi



def extract_youtube_content_online(url: str, prompt: str = "Please summarize the video in bullet points.", model: str = "gemini-2.5-flash"):

    """Extracts transcript from a YouTube video using the Gemini API (online method)."""

    try:

        api_key = get_api_key("GEMINI_API_KEY")

        if not api_key:

            raise ValueError("Gemini API key not found. Please set it in secrets.json")



        client = genai.Client(api_key=api_key)

        

        # The Gemini API can directly process YouTube URLs

        response = client.models.generate_content(

            model=model,

            contents=types.Content(

                parts=[

                    types.Part(file_data=types.FileData(file_uri=url)),

                    types.Part(text=prompt)

                ]

            )

        )

        return response.text

    except Exception as e:

        return f"Error extracting transcript with Gemini: {e}"



def extract_youtube_content_offline(url: str):

    """Extracts the transcript from a YouTube video using the youtube-transcript-api library (offline method)."""

    try:
        video_id = None
        if "v=" in url:
            video_id = url.split("v=")[1].split("&")[0]
        elif "youtu.be/" in url:
            video_id = url.split("youtu.be/")[1].split("?")[0]
        if not video_id:
            raise ValueError("Could not extract video ID from the URL.")

        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)

        transcript_text = " ".join([item['text'] for item in transcript_list])

        return transcript_text

    except Exception as e:

        return f"Error fetching transcript: {e}"





def extract_youtube_content(url: str, method: str = "online", **kwargs):

    """Facade function to choose between online (Gemini) and offline (youtube-transcript-api) YouTube content extraction."""

    if method == "offline":

        return extract_youtube_content_offline(url)

    else: # online is the default
        # ireally love kwargs 
        # really love them
        prompt = kwargs.get("prompt", "Please summarize the video in bullet points.")
        model = kwargs.get("model", "gemini-2.5-flash")

        return extract_youtube_content_online(url, prompt, model)



# --- AI Generation ---



def generate_local_llm(text, model):
    """Generates content using a local, OpenAI-compatible LLM endpoint (e.g., Ollama)."""
    from config import get_setting
    
    url = get_setting("local_llm_url", "http://localhost:11434")
    api_path = get_setting("local_llm_api_path", "/api/chat")  # Default to Ollama's API path
    full_url = url.rstrip('/') + api_path

    headers = {"Content-Type": "application/json"}
    
    # Adapt payload for Ollama vs. standard OpenAI-compatible endpoints
    if "ollama" in url or api_path == "/api/chat":  # Heuristic for Ollama
        data = {
            "model": model,
            "messages": [{"role": "user", "content": text}],
            "stream": False
        }
    else:  # OpenAI-compatible
        data = {
            "model": model,
            "messages": [{"role": "user", "content": text}]
        }

    class MockResponse:
        """A mock response object to mimic the structure of the Gemini API response."""
        def __init__(self, text):
            self.text = text

    try:
        print(f"Sending request to local LLM at {full_url} with model {model}")
        # Added a 10-minute timeout to prevent hanging indefinitely
        response = requests.post(full_url, headers=headers, json=data, timeout=600)
        response.raise_for_status()
        
        response_json = response.json()
        content = ""

        # Safely parse content from the response based on the API format
        if "ollama" in url or api_path == "/api/chat":
            content = response_json.get("message", {}).get("content", "")
        else:
            choices = response_json.get("choices", [])
            if choices and isinstance(choices, list) and len(choices) > 0:
                content = choices[0].get("message", {}).get("content", "")

        if not content:
            print("Local LLM response was empty or malformed.")
            return MockResponse("Error: Local LLM response was empty or malformed.")

        return MockResponse(content)

    except requests.exceptions.Timeout:
        print("Error: The request to the local LLM timed out after 10 minutes.")
        return MockResponse("Error: The request to the local LLM timed out.")
    except requests.exceptions.RequestException as e:
        print(f"Error calling local LLM API: {e}")
        return MockResponse(f"Error calling local LLM API: {e}")
    except Exception as e:
        print(f"Error processing local LLM response: {e}")
        return MockResponse(f"Error processing local LLM response: {e}")



def generate_gemini(text, model):

    """Generates content using the Gemini API or a local LLM based on settings."""


    engine = get_setting("generation_engine", "Gemini API")
    # Route to the local LLM if configured
    if engine == "Local LLM (Ollama)":
        local_model_name = get_setting("local_llm_model", "gemma:2b")

        return generate_local_llm(text, model=local_model_name)

    # --- Original Gemini API Logic ---

    api_key = get_api_key("GEMINI_API_KEY")

    if not api_key:

        raise ValueError("Gemini API key not found. Please set it in secrets.json")

    client = genai.Client(api_key=api_key)

    try:
        response = client.models.generate_content(model=model, contents=text)
        return response
    except Exception as e:
        print(f"Error occurred while generating content: {e}")
        
        class MockResponse:

            def __init__(self, text):

                self.text = text

        return MockResponse(f"Error generating content with Gemini: {e}")

def generate_podcast_script(text: str, engine_mode: str, model: str, 

                             enable_2speaker_mode: bool = False,) -> str | None:

    """Generates a podcast script from the given text using the specified model and speaker mode."""

    print(f"Generating podcast script with Gemini (Speaker Mode: {enable_2speaker_mode})...")

    prompt = prompter(engine_mode, enable_2speaker_mode)



    response = generate_gemini(prompt + text, model=model)

    if response and response.text:

        # Clean up the generated script text

        script_text = response.text.strip()

        if script_text.startswith('```') and script_text.endswith('```'):

            lines = script_text.split('\n')

            if len(lines) > 2:

                first_line = lines[0].strip().lower()

                if first_line.startswith('```'):

                    script_text = '\n'.join(lines[1:-1]).strip()

        

        if engine_mode == "Kokoro TTS":

            script_text = script_text.replace("*", "")

        

        print(f"Successfully generated {'2-speaker' if enable_2speaker_mode else '1-speaker'} script for {engine_mode} engine.")

        return script_text

    else:

        print(f"Error: Failed to generate script from Gemini for {'2-speaker' if enable_2speaker_mode else '1-speaker'} ({engine_mode}).")

        return None



def generate_cards(text, prompt, model_name : str):

    """Generates Anki cards from the given text using the specified prompt and model."""

    print(f"Generating Anki cards...")

    full_prompt = prompt + text

    response = generate_gemini(full_prompt, model=model_name)

    if not response or not response.text:

        print("Failed to get a valid response from Gemini for card generation.")

        return []
    # Parse the response text into cards
    lines = response.text.split("\n")
    cards = []

    for line in lines:

        if line.strip().lower().startswith("front|") or line.strip().lower().startswith("---"):

            continue

        parts = line.split("|")

        if len(parts) >= 2:

            cards.append(parts)

    return cards



# --- TTS Generation ---

MODEL_DIR = "models"

def initilizte_kokoro():
    # Define paths to local model files

    print(MODEL_DIR)
    CONFIG_FILE = os.path.join(MODEL_DIR, "config.json")

    MODEL_FILE = os.path.join(MODEL_DIR, "kokoro-v1_0.pth")
    # TODO get_setting
    speaker_1 = get_setting("kokoro_voice_1", "af_heart.pt")
    speaker_2 = get_setting("kokoro_voice_2", "af_bella.pt")
    print(
        f"{speaker_1 = }", 
        f"{speaker_2 = }"
    )
    VOICE_FILE_1 = os.path.join(MODEL_DIR, speaker_1)

    VOICE_FILE_2 = os.path.join(MODEL_DIR, speaker_2)
    # Global initialization of the Kokoro TTS pipeline from local files
    print("Initializing Kokoro TTS pipeline from local files...")
    try:
        if not all(os.path.exists(f) for f in [CONFIG_FILE, MODEL_FILE, VOICE_FILE_1, VOICE_FILE_2]):

            # TODO make it robust to make the folder and files
            raise FileNotFoundError("One or more model files not found in the 'models' directory.")


        # config=CONFIG_FILE, repo_id = 
        k_model = KModel(config = CONFIG_FILE, model=MODEL_FILE)
        return KPipeline(lang_code='a', model=k_model)
    except Exception as e:
        print(f"Failed to initialize Kokoro TTS pipeline from local files: {e}")
        print("Please ensure you have downloaded the model files and placed them in the 'models' directory.")
        return 


def generate_mindmap_with_gemini(text_content,model = "gemini-2.5-flash", output_filename = "test.dot"):
    """
    Generates mindmap concepts using Gemini and creates a DOT-language mindmap.
    :param text_content: Text extracted from a PDF or other source.
    :param api_key: Google Gemini API key.
    :return: pdf, specific format image data as a string.
    """

    MINDMAP_PROMPT = f"""
    Based on the following text, generate a concise, hierarchical mindmap structure.

**CRITICAL INSTRUCTIONS:**
1.  **Output Format:** The output MUST be a single, valid JSON object. Do not include any other text, code fencing (```json), or explanations before or after the JSON.
2.  **JSON Structure:**
    * The root object must represent the main topic of the text.
    * Each node must have a "name" string.
    * Nodes can have an optional "children" array containing sub-nodes.
3.  **Content Focus:** Extract only the core academic concepts, theories, models, definitions, and key arguments. Keep the "name" fields concise.
4.  **Exclusions:** You MUST ignore and exclude all non-academic metadata. This includes:
    * Author names
    * Publisher information
    * ISBNs, SNIs, or any identifiers
    * Page numbers
    * Publication dates
    * Prefaces, indexes, or bibliographies.
5.  **Hierarchy:** The JSON structure must represent the logical parent-child relationships of the concepts.

**Example Format:**
{{
  "name": "Main Topic",
  "children": [
    {{
      "name": "Concept 1",
      "children": [
        {{ "name": "Sub-concept 1.1" }},
        {{ "name": "Sub-concept 1.2" }}
      ]
    }},
    {{ "name": "Concept 2" }}
  ]
}}

**Text to Analyze:**
---
{text_content}
---
    """
    try:
        response = generate_gemini(MINDMAP_PROMPT, model=model)
        # Clean the response to make sure it's valid JSON
        json_str = response.text.strip().replace("```json", "").replace("```", "").strip()
        concepts_data = json.loads(json_str)
        return parse_mindmap(concepts_data, output_filename)
    except (json.JSONDecodeError, Exception) as e:
        print(f"An error occurred with the Gemini API or JSON parsing: {e}")
        # It's helpful to see the raw response when debugging
        if 'response' in locals():
            print("--- Gemini's raw response ---")
            print(response)
            print("-----------------------------")
        return ""
def dot_to_hiercial_markdown():
    pass
from typing import Any
def parse_mindmap(concepts_data: Any, output_filename: str = 'forgotten so named myself', ):
    if not concepts_data:
        print("Error: No concept data provided.")
        return None
    try:
        root_name = concepts_data['name']
    except KeyError:
        print("Error: Concept data is missing the root 'name' key.")
        return None
    import graphviz
    dot = graphviz.Digraph(comment=root_name)
    
    # --- KEY IMPROVEMENTS START HERE ---
    
    dot.attr(
        # 1. Use 'twopi' for a radial layout or 'dot' for top-down
        layout='twopi', 
        
        # 2. Set the root node (critical for 'twopi')
        root=root_name,
        
        # 3. Increase spacing between "levels"
        ranksep='1.5',
        
        # 4. Make edges curved and softer
        splines='curved', 
        
        # 5. Prevent nodes from overlapping (can also use 'scale')
        overlap='false' 
    )
    
    # --- END OF IMPROVEMENTS ---

    COLORS = ('#E0BBE4', '#957DAD', '#FFC72C', '#DD6B29', '#D291BC')

    def add_node_and_edges(node_data, parent_name=None, level=0):
        """
        A recursive helper function to add nodes and edges to the graph.
        """
        try:
            node_name = node_data['name']
        except KeyError:
            print(f"Error: Node data is missing 'name' key: {node_data}")
            return
            
        color = COLORS[level % len(COLORS)]
        if level == 0:  # Root node
            attrs = {
                'shape': 'ellipse', 'style': 'filled',
                'fillcolor': color, 'fontsize': '20',
                'fontname': 'Helvetica-Bold'
            }
        elif level == 1:  # Main concepts
            attrs = {
                'shape': 'box', 'style': 'filled,rounded',
                'fillcolor': color, 'fontsize': '16',
                'fontname': 'Helvetica'
            }
        else:  # Sub-concepts
            attrs = {
                'shape': 'rectangle', 'style': 'filled',
                'fillcolor': color, 'fontsize': '12',
                'fontname': 'Helvetica'
            }

        dot.node(node_name, **attrs)

        if parent_name:
            # Softer edge color
            dot.edge(parent_name, node_name, color="#444444") 

        if 'children' in node_data and node_data['children']:
            for child_data in node_data['children']:
                add_node_and_edges(child_data, parent_name=node_name, level=level + 1)

    add_node_and_edges(concepts_data, parent_name=None, level=0)

    try:
        # get_setting app_pyside.py
        output_format = 'pdf' # TODO custumize output format, 
        dot.render(output_filename, format=output_format, view=True)
        dot.render(output_filename, format='png')  # Also save as PNG

        print(f"✅ Mindmap successfully saved to {output_filename}.{output_format}")
    except Exception as e:
        print(f"Error rendering graphviz file: {e}")
    return dot
       
def _clean_text_for_tts(text: str) -> str:

    """Cleans text for TTS, removing special characters or formatting not suitable for speech."""

    text = text.replace('*', '').replace('_', '').strip()

    return text



MODELS_DIR = 'models'
def generate_audio_kokoro(script: str, output_filename: str, 
                          enable_2speaker_mode: bool = False, 
                          audio_book_mode: bool = False,
                          voice1_filename: str = "af_heart.pt", 
                          voice2_filename: str = "af_bella.pt"):
    """
    Generates audio from a script using the Kokoro TTS engine with local models.
    Supports different modes for podcasts (single/dual speaker) and audiobooks.
    """
    kokoro_pipeline = initilizte_kokoro()
    if kokoro_pipeline is None:
        raise RuntimeError("Kokoro TTS pipeline is not initialized / available. Please check the console for errors during initialization.")
    
    SAMPLING_RATE = 24_000
    
    try:
        voice1_path = os.path.join(MODEL_DIR, voice1_filename)
        voice2_path = os.path.join(MODEL_DIR, voice2_filename)
        voice1 = torch.load(voice1_path)
        voice2 = torch.load(voice2_path)
    except Exception as e:
        raise RuntimeError(f"Failed to load local voice files: {e}")

    final_audio_chunks = []

    if audio_book_mode:
        print("Generating Kokoro Audio in Audiobook Mode (Offline)...")
        # For audiobooks, process text page by page (split by '---').
        script_sections = script.split('---')
        for section in tqdm(script_sections, desc="Generating Audiobook Audio"):
            if not section.strip():
                continue
            try:
                clean_text = _clean_text_for_tts(section)
                # Split each page into sentences for smoother generation.
                generator = kokoro_pipeline(clean_text, voice=voice1, split_pattern=r'[.?!]\s*')
                chunks = [audio for _, _, audio in generator]
                if chunks:
                    final_audio_chunks.append(np.concatenate(chunks))
                    # Add a pause between pages.
                    final_audio_chunks.append(np.zeros(int(0.7 * SAMPLING_RATE), dtype=np.float32))
            except Exception as e:
                print(f"Error during TTS generation for audiobook section: '{section[:30]}...'. Error: {e}")

    elif enable_2speaker_mode:
        print("Generating Kokoro Audio in 2-Speaker Mode (Offline)...")
        script_lines = script.strip().split('\n')
        current_speaker_voice = voice1
        for line in tqdm(script_lines, desc="Generating Kokoro Audio (2 Speakers)"):
            if not line.strip():
                continue
            
            if line.startswith("SPEAKER1:"):
                clean_text = line.replace("SPEAKER1:", "").strip()
                current_speaker_voice = voice1
            elif line.startswith("SPEAKER2:"):
                clean_text = line.replace("SPEAKER2:", "").strip()
                current_speaker_voice = voice2
            else:
                clean_text = line.strip() # Continue with last speaker if line is not marked

            if not clean_text:
                continue
            
            try:
                cleaned_section = _clean_text_for_tts(clean_text)
                generator = kokoro_pipeline(cleaned_section, voice=current_speaker_voice, split_pattern=r'[.?!]\s*')
                chunks = [audio for _, _, audio in generator]
                if chunks:
                    final_audio_chunks.append(np.concatenate(chunks))
                    # Standard pause between lines.
                    final_audio_chunks.append(np.zeros(int(0.3 * SAMPLING_RATE), dtype=np.float32))
            except Exception as e:
                print(f"Error during TTS generation for line: '{line[:50]}...'. Error: {e}")
    
    else: # Single-speaker podcast mode
        print("Generating Kokoro Audio in Single Speaker Mode (Offline)...")
        # For single-speaker podcasts, the script is split by paragraph markers.
        script_sections = script.split('---')
        for section in tqdm(script_sections, desc="Generating Kokoro Audio (Single Speaker)"):
            if not section.strip():
                continue
            try:
                clean_text = _clean_text_for_tts(section)
                generator = kokoro_pipeline(clean_text, voice=voice1, split_pattern=r'\n+')
                chunks = [audio for _, _, audio in generator]
                if chunks:
                    final_audio_chunks.append(np.concatenate(chunks))
                    # Pause between paragraphs.
                    final_audio_chunks.append(np.zeros(int(0.5 * SAMPLING_RATE), dtype=np.float32))
            except Exception as e:
                print(f"Error during TTS generation for section: '{section[:30]}...'. Error: {e}")

    if final_audio_chunks:
        print("\nAssembling final audio file...")
        full_audio = np.concatenate(final_audio_chunks)
        sf.write(output_filename, full_audio, SAMPLING_RATE)
        print(f"\nSuccessfully saved podcast to {output_filename}")
    else:
        raise Exception("No audio chunks were generated.")



def get_elevenlabs_voices(api_key: str):

    """Fetches available voices from the ElevenLabs API."""

    url = "https://api.elevenlabs.io/v1/voices"

    headers = {"xi-api-key": api_key}

    try:

        response = requests.get(url, headers=headers)

        if response.status_code == 200:

            voices = response.json()['voices']

            return {voice['name']: voice['voice_id'] for voice in voices}

        else:

            return {}

    except requests.exceptions.RequestException as e:

        print(f"Error fetching ElevenLabs voices: {e}")

        return {}



def generate_audio_elevenlabs(script: str, api_key: str, output_filename: str, voice1_id: str = "JBFqnCBsd6RMkjVDRZzb", voice2_id: str = "Z3R5wn05IrDiVCyEkUrK"):

    """Generates audio from a script using the ElevenLabs API, handling multiple speakers."""

    print("Generating audio with ElevenLabs...")

    

    headers = {

        "Accept": "audio/mpeg",

        "Content-Type": "application/json",

        "xi-api-key": api_key

    }

    

    script_lines = [line.strip() for line in script.strip().split('\n') if line.strip()]

    audio_chunks = []



    # Check if this is a single-speaker or two-speaker script

    is_two_speaker = any(line.startswith("SPEAKER1:") or line.startswith("SPEAKER2:") for line in script_lines)



    if is_two_speaker:

        print("Generating in 2-Speaker Mode...")

        for line in tqdm(script_lines, desc="Generating ElevenLabs Audio (2 Speakers)"):

            voice_id = None

            text_to_speak = ""

            if line.startswith("SPEAKER1:"):

                voice_id = voice1_id

                text_to_speak = line.replace("SPEAKER1:", "").strip()

            elif line.startswith("SPEAKER2:"):

                voice_id = voice2_id

                text_to_speak = line.replace("SPEAKER2:", "").strip()

            else:

                continue



            if not text_to_speak: continue

            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

            data = {

                "text": text_to_speak,

                "model_id": "eleven_multilingual_v2",

                "voice_settings": {

                    "stability": 0.5,

                    "similarity_boost": 0.75

                }

            }

            

            try:

                response = requests.post(url, json=data, headers=headers)

                if response.status_code == 200:

                    audio_chunks.append(response.content)

                else:

                    print(f"ElevenLabs API Error for line: '{line[:50]}...'. Status: {response.status_code}, Message: {response.text}")

            except requests.exceptions.RequestException as e:

                print(f"Network error for line: '{line[:50]}...'. Error: {e}")



    else: # Single speaker mode

        print("Generating in Single Speaker Mode...")

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice1_id}"

        data = {

            "text": script,

            "model_id": "eleven_multilingual_v2",

            "voice_settings": {

                "stability": 0.5,

                "similarity_boost": 0.75

            }

        }

        try:

            response = requests.post(url, json=data, headers=headers)

            if response.status_code == 200:

                audio_chunks.append(response.content)

            else:

                print(f"ElevenLabs API Error for single speaker script. Status: {response.status_code}, Message: {response.text}")

        except requests.exceptions.RequestException as e:

            print(f"Network error for single speaker script. Error: {e}")



    if audio_chunks:

        print("Assembling final audio file...")

        with open(output_filename, "wb") as f:

            for chunk in audio_chunks:

                f.write(chunk)

        print(f"Successfully saved podcast to {output_filename}")

    else:

        raise Exception("No audio chunks were generated from ElevenLabs.")
def wave_file(filename, pcm, channels=1, rate=24000, sample_width=2):
    """Saves raw PCM audio data to a WAV file."""
    try:
        with wave.open(filename, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(rate)
            wf.writeframes(pcm)
    except Exception as e:
        print(f"Error saving wave file {filename}: {e}")
        raise
# new
def generate_audio_gemini(script: str, output_filename: str, 
                          enable_2speaker_mode: bool = False,
                          voice1_id: str = "Kore", 
                          voice2_id: str = "Puck",
                          model: str = "gemini-2.5-flash-preview-tts"):
    """
    Generates audio from a script using the Gemini TTS API.
    [UPDATED]: Uses the correct client.models.generate_content API.
    [UPDATED]: Outputs to .wav format.
    [UPDATED]: 2-speaker mode uses a single efficient API call.
    """
    print(f"Generating audio with Gemini TTS... (2-Speaker: {enable_2speaker_mode})")
    
    api_key = get_api_key("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Gemini API key not found. Please set it in secrets.json")

    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        raise ValueError(f"Failed to initialize Gemini client: {e}")

    audio_chunks = []
    
    # --- Ensure output filename is .wav ---
    if not output_filename.lower().endswith(".wav"):
        output_filename = os.path.splitext(output_filename)[0] + ".wav"
        print(f"Output format is WAV. Saving to: {output_filename}")

    if enable_2speaker_mode:
        print("Generating in 2-Speaker Mode (Single Call)...")
        # Your script already has "SPEAKER1:" and "SPEAKER2:",
        # which is what the multi-speaker config requires.
        try:
            # Configure voices for SPEAKER1 and SPEAKER2
            config = types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                        speaker_voice_configs=[
                            types.SpeakerVoiceConfig(
                                speaker='SPEAKER1', # Must match the script
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=voice1_id,
                                    )
                                )
                            ),
                            types.SpeakerVoiceConfig(
                                speaker='SPEAKER2', # Must match the script
                                voice_config=types.VoiceConfig(
                                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                        voice_name=voice2_id,
                                    )
                                )
                            ),
                        ]
                    )
                )
            )
            
            # Pass the entire script. The API will parse the "SPEAKER1:" tags.
            response = client.models.generate_content(
                model=model,
                contents=script, # Send the whole script
                config=config
            )
            
            data = response.candidates[0].content.parts[0].inline_data.data
            audio_chunks.append(data)
            
        except Exception as e:
            print(f"Gemini TTS API Error for 2-speaker script: {e}")
            raise Exception(f"Failed to generate 2-speaker audio: {e}")

    else: # Single speaker mode
        print("Generating in Single Speaker Mode (by Paragraph)...")
        
        # Configure the single voice
        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice1_id,
                )
            )
        )
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=speech_config
        )

        # Split by paragraph and process one by one to avoid token limits
        script_sections = script.split('---') 
        
        for section in tqdm(script_sections, desc="Generating Gemini TTS Audio (Single Speaker)"):
            clean_text = section.strip()
            if not clean_text:
                continue
            
            try:
                # Call API for each section
                response = client.models.generate_content(
                    model=model,
                    contents=clean_text, # Send one section at a time
                    config=config
                )
                data = response.candidates[0].content.parts[0].inline_data.data
                audio_chunks.append(data)
            except Exception as e:
                print(f"Gemini TTS API Error for section: '{clean_text[:50]}...'. Error: {e}")
                # Don't stop, just skip this chunk

    # --- Assemble the final audio file ---
    if audio_chunks:
        print("Assembling final .wav file...")
        full_audio_pcm = b"".join(audio_chunks)
        wave_file(output_filename, full_audio_pcm) # Use the new helper
        print(f"Successfully saved podcast to {output_filename}")
    else:
        raise Exception("No audio chunks were successfully generated from Gemini TTS.")
# --- File Output ---



def ocr_and_save_pdf(input_path: str, output_path: str):

    """Runs OCR on a PDF and saves it as a new searchable PDF."""

    print(f"Starting OCR on {input_path}...")

    try:

        ocrmypdf.ocr(input_path, output_path, force_ocr=True)

        print(f"Successfully created OCR'd PDF at {output_path}")

    except Exception as e:

        print(f"An error occurred during OCR processing: {e}")

        raise e



def convert_to_epub(file_path: str, output_path: str, ocr_enabled: bool, page_ranges: list | None = None):

    """Converts a given PDF or PPTX file to EPUB format."""

    print(f"Starting EPUB conversion for {os.path.basename(file_path)}...")

    

    book = epub.EpubBook()

    book.set_title(os.path.splitext(os.path.basename(file_path))[0])

    book.set_language('en')



    chapters = []

    

    # --- Process based on file type ---

    if file_path.lower().endswith('.pdf'):

        doc = pymupdf.open(file_path)

        

        pages_to_process = range(len(doc))

        if page_ranges:

            # If specific page ranges are given, flatten them into a set of page numbers

            pages_to_process = set()

            for start, end in page_ranges:

                for i in range(start, end + 1):

                    if i < len(doc):

                        pages_to_process.add(i)

            pages_to_process = sorted(list(pages_to_process))



        for i in tqdm(pages_to_process, desc="Converting PDF to EPUB"):

            page = doc.load_page(i)

            page_text = page.get_text("html") # Get text with basic HTML structure

            

            # OCR if page has no text and OCR is enabled

            if not page.get_text().strip() and ocr_enabled:

                print(f"Page {i+1} has no text, performing OCR...")

                pix = page.get_pixmap()

                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                page_text = pytesseract.image_to_string(img)

                page_text = f"<p>{page_text}</p>" # Wrap OCR text in paragraph tags



            # Add images from the page

            img_counter = 0

            for img_info in page.get_images(full=True):

                xref = img_info[0]

                base_image = doc.extract_image(xref)

                image_bytes = base_image["image"]

                

                img_filename = f'page_{i+1}_img_{img_counter}.{base_image["ext"]}'

                epub_image = epub.EpubImage(uid=img_filename, file_name=f'images/{img_filename}', media_type=f'image/{base_image["ext"]}', content=image_bytes)

                book.add_item(epub_image)

                

                page_text += f'<p><img src="images/{img_filename}" alt="Image from page {i+1}"/></p>'

                img_counter += 1



            chapter = epub.EpubHtml(title=f'Page {i+1}', file_name=f'page_{i+1}.xhtml', lang='en')

            chapter.content = page_text

            chapters.append(chapter)

        doc.close()



    elif file_path.lower().endswith('.pptx'):
        prs = Presentation(file_path)
        for i, slide in enumerate(tqdm(prs.slides, desc="Converting PPTX to EPUB")):

            slide_html = ""

            for shape in slide.shapes:

                if hasattr(shape, "text"):

                    slide_html += f"<p>{shape.text}</p>"

                # Note: Image extraction from PPTX is more complex and not added in this version.

            chapter = epub.EpubHtml(title=f'Slide {i+1}', file_name=f'slide_{i+1}.xhtml', lang='en')

            chapter.content = slide_html

            chapters.append(chapter)



    # --- Assemble the EPUB ---

    for chapter in chapters:
        book.add_item(chapter)

    book.toc = chapters

    book.add_item(epub.EpubNcx())

    book.add_item(epub.EpubNav())



    # Defines the order of chapters

    book.spine = ['nav'] + chapters

    epub.write_epub(output_path, book, {})

    print(f"Successfully created EPUB file at {output_path}")
def _call_gemini_tts(text: str, voice: str, model: str, client: genai.Client):
    """
    Helper function to make a single, robust call to the Gemini TTS API.
    Returns audio bytes or None if failed.
    """
    if not text:
        return None
    try:
        # This assumes client.text_to_speech is a valid method, as in your original code.
        response = client.text_to_speech(model=model, text=text, voice=voice)
        return response.audio
    except Exception as e:
        # Log the error but don't stop the whole generation process
        print(f"Gemini TTS API Error for text: '{text[:50]}...'. Error: {e}")
        return None

def create_anki_package(deck_name, basic_cards, cloze_cards, output_path):

    """Creates an Anki deck package (.apkg) from lists of basic and cloze cards."""

    if not deck_name:

        deck_name = "Generated Deck"

    

    # Use a random deck ID

    deck = genanki.Deck(random.randrange(1 << 30, 1 << 31), deck_name)

    

    # Define Anki card models

    basic_model = genanki.Model(
        1645362923, 'Basic with Notes',
        fields=[{'name': 'Front'}, {'name': 'Back'}, {'name': 'Notes'}],
        templates=[{'name': 'Card 1', 'qfmt': '{{Front}}', 'afmt': '{{FrontSide}}<hr id=answer>{{Back}}<br>{{Notes}}'}]
    )
    cloze_model = genanki.Model(
        1983257410, 'Cloze (Med)',
        fields=[{'name': 'Text'}, {'name': 'Extra'}],
        templates=[{'name': 'Cloze Card', 'qfmt': '{{cloze:Text}}', 'afmt': '{{cloze:Text}}<br>{{Extra}}'}],
        model_type=genanki.Model.CLOZE
    )
    # Add notes to the deck
    for card_parts in basic_cards:
        if len(card_parts) >= 2:
            deck.add_note(genanki.Note(model=basic_model, fields=card_parts[:3] if len(card_parts) > 2 else card_parts + ['']))
    for card_parts in cloze_cards:
        if len(card_parts) >= 1:
            deck.add_note(genanki.Note(model=cloze_model, fields=card_parts[:2] if len(card_parts) > 1 else card_parts + ['']))
    if not deck.notes:
        print("No notes were generated, skipping deck creation.")
        return False
    # Write the deck to an .apkg file
    genanki.Package(deck).write_to_file(output_path)
    return True
