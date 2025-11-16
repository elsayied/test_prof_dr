import streamlit as st
import os
import json
import threading
import time
import re
from backend import (
    extract_pdf_content,
    extract_pptx_content,
    extract_docx_content,
    extract_youtube_content,
    generate_podcast_script,
    generate_audio_kokoro,
    generate_audio_elevenlabs,
    ocr_and_save_pdf,
    generate_cards,
    create_anki_package,
    extract_pdf_chapters,
    parse_toc_with_ai,
    BASIC_CARDS_PROMPT,
    HIGH_YIELD_BASIC_CARDS_PROMPT,
    CLOZE_EXTRA_PROMPT,
    convert_to_epub
)

def main():
    st.title("Podcaster Pro")

    # ---- State and Config Variables ----
    if 'file_options' not in st.session_state:
        st.session_state.file_options = {}
    if 'ocr_var' not in st.session_state:
        st.session_state.ocr_var = True
    if 'series_mode_var' not in st.session_state:
        st.session_state.series_mode_var = False

    # ---- UI Frames and Tabs ----
    source_type = st.sidebar.selectbox("Source Type", ["FILE", "YouTube"])
    
    if source_type == "FILE":
        uploaded_files = st.sidebar.file_uploader("Choose files", accept_multiple_files=True, type=['pdf', 'pptx', 'docx'])
        if uploaded_files:
            for uploaded_file in uploaded_files:
                if uploaded_file.name not in st.session_state.file_options:
                    st.session_state.file_options[uploaded_file.name] = {'range': None, 'file': uploaded_file}
    else:
        youtube_url = st.sidebar.text_input("YouTube URL")
        if youtube_url:
            st.session_state.file_options['youtube'] = {'range': None, 'url': youtube_url}


    st.session_state.ocr_var = st.sidebar.checkbox("Enable OCR (for text in images)", value=st.session_state.ocr_var)

    st.sidebar.subheader("Selected Files")
    for file_name, options in st.session_state.file_options.items():
        st.sidebar.write(file_name)
        if file_name.lower().endswith(".pdf"):
            options['range'] = st.sidebar.text_input(f"Range for {file_name} (e.g. 2-6,11,13-40)", key=f"range_{file_name}")


    tab1, tab2, tab3 = st.tabs(["🎙️ Podcaster", "🃏 Anki Generator", "🛠️ Utilities"])

    with tab1:
        create_podcaster_tab()
    with tab2:
        create_anki_tab()
    with tab3:
        create_utilities_tab()

def create_podcaster_tab():
    st.header("Podcaster")
    st.session_state.series_mode_var = st.checkbox("Generate Podcast Series from Table of Contents", value=st.session_state.series_mode_var)

    if st.session_state.series_mode_var:
        create_podcast_series_tab()
    else:
        create_standard_podcast_tab()

def create_standard_podcast_tab():
    speaker_mode = st.selectbox("Speaker Mode", ["1 Speaker", "2 Speakers"])
    
    if st.button("1. Extract Text & Generate Podcast Script"):
        full_text = ""
        for file_name, options in st.session_state.file_options.items():
            if 'file' in options:
                uploaded_file = options['file']
                file_path = os.path.join("/tmp", uploaded_file.name)
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getbuffer())
                
                file_range = options.get('range')
                full_text += get_text_from_file(file_path, st.session_state.ocr_var, file_range) + "\n\n"

        if not full_text:
            st.error("No text extracted.")
            return

        with st.spinner("Generating script..."):
            script = generate_podcast_script(full_text, speaker_mode=speaker_mode)
            if isinstance(script, dict) and "error" in script:
                st.error(f"Error generating script: {script['error']}")
                return
            
            st.session_state.script = script
            st.text_area("Generated Script", script, height=300)

    if 'script' in st.session_state:
        tts_engine = st.selectbox("TTS Engine", ["Kokoro TTS", "ElevenLabs"])
        if tts_engine == "ElevenLabs":
            api_key = st.text_input("ElevenLabs API Key", type="password")
            st.session_state.api_key = api_key

        if st.button("2. Generate Podcast Audio"):
            with st.spinner("Generating audio..."):
                output_filename = "/tmp/podcast.mp3"
                try:
                    if tts_engine == "Kokoro TTS":
                        generate_audio_kokoro(st.session_state.script, output_filename)
                    elif tts_engine == "ElevenLabs":
                        if not st.session_state.get('api_key'):
                            st.error("ElevenLabs API Key is required.")
                            return
                        
                        segments = []
                        if speaker_mode == "2 Speakers":
                            host_voice_id = "JBFqnCBsd6RMkjVDRZzb" 
                            guest_voice_id = "Aw4FAjKCGjjNkVhN1Xmq"
                            
                            host_lines = st.session_state.script.get("host", [])
                            guest_lines = st.session_state.script.get("guest", [])

                            max_len = max(len(host_lines), len(guest_lines))
                            for i in range(max_len):
                                if i < len(host_lines):
                                    segments.append((host_lines[i], host_voice_id))
                                if i < len(guest_lines):
                                    segments.append((guest_lines[i], guest_voice_id))
                        else:
                            default_voice_id = "JBFqnCBsd6RMkjVDRZzb"
                            segments.append((st.session_state.script, default_voice_id))
                        
                        generate_audio_elevenlabs(segments, st.session_state.api_key, output_filename)

                    st.audio(output_filename)
                    with open(output_filename, "rb") as f:
                        st.download_button("Download Podcast", f, file_name="podcast.mp3")

                except Exception as e:
                    st.error(f"Error generating audio: {e}")

def create_podcast_series_tab():
    st.write("Podcast Series Generation")
    toc_text = st.text_area("Paste Table of Contents below:", height=150)
    
    if st.button("Generate Podcast Series"):
        if not st.session_state.file_options or len(st.session_state.file_options) != 1:
            st.error("Please select a single PDF file for Podcast Series generation.")
            return
        
        file_name = list(st.session_state.file_options.keys())[0]
        uploaded_file = st.session_state.file_options[file_name]['file']
        input_pdf_path = os.path.join("/tmp", uploaded_file.name)
        with open(input_pdf_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        if not toc_text.strip():
            st.error("Please paste the Table of Contents.")
            return

        with st.spinner("Generating podcast series..."):
            try:
                import fitz
                doc = fitz.open(input_pdf_path)
                total_pages = len(doc)
                doc.close()
                chapters = parse_toc_with_ai(toc_text, total_pages)

                if not chapters:
                    st.error("AI could not determine chapters from the ToC.")
                    return

                st.write(f"Found {len(chapters)} chapters. Starting generation...")
                
                for i, chapter in enumerate(chapters):
                    title = chapter['title']
                    start_page = chapter['start_page']
                    end_page = chapter['end_page']
                    
                    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
                    output_filename = f"/tmp/{i+1:02d}_{safe_title}.mp3"

                    st.write(f"Processing Chapter {i+1}/{len(chapters)}: {title}")

                    chapter_text = ""
                    doc = fitz.open(input_pdf_path)
                    for page_num in range(start_page, end_page + 1):
                        if page_num < len(doc):
                            chapter_text += doc.load_page(page_num).get_text() + "\n"
                    doc.close()

                    if not chapter_text.strip():
                        st.write(f"Skipping chapter '{title}' (no text found).")
                        continue

                    script = generate_podcast_script(chapter_text)
                    
                    tts_engine = st.selectbox("TTS Engine", ["Kokoro TTS", "ElevenLabs"], key=f"tts_{i}")
                    if tts_engine == "Kokoro TTS":
                        generate_audio_kokoro(script, output_filename)
                    elif tts_engine == "ElevenLabs":
                        api_key = st.text_input("ElevenLabs API Key", type="password", key=f"api_key_{i}")
                        if not api_key:
                            st.error("ElevenLabs API key is required. Stopping series generation.")
                            return
                        generate_audio_elevenlabs(script, api_key, output_filename)
                    
                    st.audio(output_filename)
                    with open(output_filename, "rb") as f:
                        st.download_button(f"Download {safe_title}.mp3", f, file_name=f"{safe_title}.mp3")

                st.success("Podcast series generation complete!")

            except Exception as e:
                st.error(f"An error occurred during series generation: {e}")


def create_anki_tab():
    st.header("Anki Generator")
    deck_name = st.text_input("Deck Name", "e.g., Cardiology Lecture 1")
    
    use_basic = st.checkbox("Basic (Q&A)", value=True)
    use_cloze = st.checkbox("Cloze (Fill-in-the-blank)", value=True)
    high_yield_mode = st.checkbox("High-Yield Mode (Focus on key concepts)")

    strategy_map = {
        "Auto-Detect Chapters": "auto",
        "Table of Contents": "toc",
        "Font Size Heuristic": "font",
        "Fixed Page Chunks": "page_chunks",
        "Custom Ranges": "custom_range",
        "Full Document": "full_document"
    }
    strategy_name = st.selectbox("PDF Strategy", list(strategy_map.keys()))
    strategy = strategy_map[strategy_name]
    
    strategy_param = None
    if strategy_name == "Fixed Page Chunks":
        strategy_param = st.number_input("Chunk Size (pages)", value=15)
    elif strategy_name == "Font Size Heuristic":
        strategy_param = st.number_input("Title Font Size", value=18)
    elif strategy_name == "Custom Ranges":
        strategy_param = st.text_input("Ranges (e.g. 2-6,11,13-40)")
    elif strategy_name == "AI-Assisted ToC":
        strategy_param = st.text_area("Paste Table of Contents below:", height=150)

    if st.button("Generate Anki Deck (.apkg)"):
        if not st.session_state.file_options:
            st.error("No files selected.")
            return

        if not deck_name:
            deck_name = os.path.splitext(os.path.basename(list(st.session_state.file_options.keys())[0]))[0]

        basic_prompt = HIGH_YIELD_BASIC_CARDS_PROMPT if high_yield_mode else BASIC_CARDS_PROMPT
        
        all_basic_cards = []
        all_cloze_cards = []

        with st.spinner("Generating Anki deck..."):
            try:
                for file_name, options in st.session_state.file_options.items():
                    st.write(f"Processing {file_name}...")
                    
                    uploaded_file = options['file']
                    file_path = os.path.join("/tmp", uploaded_file.name)
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())

                    text_chunks = []
                    if file_path.lower().endswith(".pdf"):
                        for title, text in extract_pdf_chapters(file_path, strategy=strategy, ocr=st.session_state.ocr_var, strategy_param=strategy_param):
                            text_chunks.append((title, text))
                    else:
                        title = os.path.basename(file_path)
                        text = get_text_from_file(file_path, st.session_state.ocr_var)
                        if text:
                            text_chunks.append((title, text))

                    for title, text_chunk in text_chunks:
                        if not text_chunk.strip(): continue
                        if use_basic:
                            st.write(f"Generating Basic cards for: {title}...")
                            all_basic_cards.extend(generate_cards(text_chunk, basic_prompt))
                        if use_cloze:
                            st.write(f"Generating Cloze cards for: {title}...")
                            all_cloze_cards.extend(generate_cards(text_chunk, CLOZE_EXTRA_PROMPT))

                if not all_basic_cards and not all_cloze_cards:
                    st.warning("No cards were generated. Anki deck not created.")
                    return

                output_path = f"/tmp/{deck_name}.apkg"
                success = create_anki_package(deck_name, all_basic_cards, all_cloze_cards, output_path)
                
                if success:
                    st.success(f"Successfully created Anki deck: {deck_name}.apkg")
                    with open(output_path, "rb") as f:
                        st.download_button("Download Anki Deck", f, file_name=f"{deck_name}.apkg")
                else:
                    st.warning("Anki deck creation failed.")

            except Exception as e:
                st.error(f"An error occurred during Anki generation: {e}")


def create_utilities_tab():
    st.header("Utilities")

    if st.button("OCR PDF & Save as Searchable PDF"):
        if not st.session_state.file_options or len(st.session_state.file_options) != 1:
            st.error("Please select a single PDF file for this utility.")
            return
        
        file_name = list(st.session_state.file_options.keys())[0]
        uploaded_file = st.session_state.file_options[file_name]['file']
        input_path = os.path.join("/tmp", uploaded_file.name)
        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        output_path = f"/tmp/{os.path.splitext(uploaded_file.name)[0]}_ocr.pdf"
        
        with st.spinner(f"Performing OCR on {uploaded_file.name}..."):
            try:
                ocr_and_save_pdf(input_path, output_path)
                st.success(f"Successfully saved OCR'd PDF to {os.path.basename(output_path)}")
                with open(output_path, "rb") as f:
                    st.download_button("Download OCR'd PDF", f, file_name=os.path.basename(output_path))
            except Exception as e:
                st.error(f"Error during OCR processing: {e}")

    if st.button("Convert File to EPUB"):
        if not st.session_state.file_options or len(st.session_state.file_options) != 1:
            st.error("Please select a single PDF or PPTX file for EPUB conversion.")
            return
        
        file_name = list(st.session_state.file_options.keys())[0]
        uploaded_file = st.session_state.file_options[file_name]['file']
        input_path = os.path.join("/tmp", uploaded_file.name)
        with open(input_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        output_path = f"/tmp/{os.path.splitext(uploaded_file.name)[0]}.epub"

        with st.spinner(f"Converting {uploaded_file.name} to EPUB..."):
            try:
                page_ranges = None
                range_str = st.session_state.file_options[file_name].get('range')
                if range_str:
                    page_ranges = parse_range_string(range_str)

                convert_to_epub(input_path, output_path, st.session_state.ocr_var, page_ranges=page_ranges)
                st.success(f"Successfully created EPUB: {os.path.basename(output_path)}")
                with open(output_path, "rb") as f:
                    st.download_button("Download EPUB", f, file_name=os.path.basename(output_path))
            except Exception as e:
                st.error(f"Error during EPUB conversion: {e}")


def get_text_from_file(file_path, ocr_enabled, file_range=None):
    """Extracts text content from various file types (PDF, PPTX, DOCX) using backend functions."""
    parsed_ranges = parse_range_string(file_range) if file_range else None
    if file_path.lower().endswith(".pdf"):
        if parsed_ranges:
            full_text_from_range = ""
            for _, chunk_text in extract_pdf_chapters(file_path, strategy="custom_range", ocr=ocr_enabled, strategy_param=parsed_ranges):
                full_text_from_range += chunk_text + "\n"
            return full_text_from_range
        else:
            return extract_pdf_content(file_path, ocr=ocr_enabled)
    elif file_path.lower().endswith(".pptx"):
        return extract_pptx_content(file_path, ocr=ocr_enabled)
    elif file_path.lower().endswith(".docx"):
        return extract_docx_content(file_path)
    return ""

def parse_range_string(range_str):
    """Parses a string like '1-5, 8, 10-12' into a list of tuples [(0,4), (7,7), (9,11)]."""
    ranges = []
    parts = range_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            start, end = part.split('-')
            ranges.append((int(start.strip()) - 1, int(end.strip()) - 1))
        else:
            page = int(part.strip()) - 1
            ranges.append((page, page))
    return ranges

if __name__ == "__main__":
    main()
