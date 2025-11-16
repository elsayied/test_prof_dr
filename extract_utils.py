# --- Content Extraction ---

def extract_pdf_content(path, ocr=False):
    """Extracts text, tables, and optionally OCRs images from a PDF file."""
    text = ""
    # Keywords to skip introductory slides
    skip_keywords = ["Professor", "Subject", "level 1", "Semester 2", "Prof.", "Lecture No:", "faculty of", "Mission and Vision of Faculty", "thank-you"]
    
    with pdfplumber.open(path) as pdf:
        fitz_doc = fitz.open(path) if ocr else None
        for i, page in enumerate(tqdm(pdf.pages, desc=f"Processing PDF: {os.path.basename(path)}")):
            page_text = page.extract_text() or ""

            if any(keyword.lower() in page_text.lower() for keyword in skip_keywords) and i < 5:
                continue

            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        page_text += "\t".join(str(cell) if cell is not None else "" for cell in row) + "\n"
                    page_text += "\n"

            if ocr and fitz_doc:
                fitz_page = fitz_doc[i]
                image_list = fitz_page.get_images(full=True)
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = fitz_doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    try:
                        image_pil = Image.open(io.BytesIO(image_bytes))
                        ocr_text = pytesseract.image_to_string(image_pil)
                        if ocr_text.strip():
                            page_text += f"\n[OCR from Image {img_index+1}]:\n{ocr_text}\n"
                    except Exception as e:
                        print(f"  Error OCR'ing embedded image {img_index+1}: {e}")
            
            text += page_text + "\n"
        
        if fitz_doc:
            fitz_doc.close()
            
    return text

def extract_docx_content(file_path: str):
    """Extracts text from a DOCX file."""
    text_content = ""
    try:
        doc = Document(file_path)
        for para in tqdm(doc.paragraphs, desc=f"Processing DOCX: {os.path.basename(file_path)}"):
            text_content += para.text + "\n"
    except Exception as e:
        print(f"Error processing DOCX file '{file_path}': {e}")
    return text_content

def extract_pptx_content(file_path: str, ocr: bool = True):
    """Extracts text from slides and optionally OCRs images in a PowerPoint file."""
    text_content = ""
    try:
        prs = Presentation(file_path)
        for i, slide in enumerate(tqdm(prs.slides, desc=f"Processing PPTX: {os.path.basename(file_path)}")):
            slide_text = ""
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    slide_text += shape.text + "\n"
                elif hasattr(shape, "has_table") and shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            slide_text += cell.text + "\t"
                        slide_text += "\n"
                
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
    """Extracts transcript from a YouTube video using Gemini."""
    try:
        api_key = get_api_key("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key not found. Please set it in secrets.json")

        client = genai.Client(api_key=api_key)
        
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
    """Extracts the transcript from a YouTube video using youtube-transcript-api."""
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
    if method == "offline":
        return extract_youtube_content_offline(url)
    else: # online is the default
        prompt = kwargs.get("prompt", "Please summarize the video in bullet points.")
        model = kwargs.get("model", "gemini-2.5-flash")
        return extract_youtube_content_online(url, prompt, model)
def _get_ocr_text_for_page(fitz_doc, page_num):
    """Helper function to perform OCR on a single page."""
    page_text = ""
    fitz_page = fitz_doc[page_num]
    image_list = fitz_page.get_images(full=True)
    for img_index, img in enumerate(image_list):
        xref = img[0]
        base_image = fitz_doc.extract_image(xref)
        image_bytes = base_image["image"]
        try:
            image_pil = Image.open(io.BytesIO(image_bytes))
            ocr_text = pytesseract.image_to_string(image_pil)
            if ocr_text.strip():
                page_text += f"\n[OCR from Image {img_index+1}]:\n{ocr_text}\n"
        except Exception as e:
            print(f"  Error OCR'ing embedded image on page {page_num + 1}: {e}")
    return page_text

def _get_chapters_from_toc(doc):
    """Attempts to parse chapters from the document's Table of Contents."""
    toc = doc.get_toc()
    if not toc: return []
    
    chapters = []
    # Filter for top-level chapters (level 1)
    top_level_chapters = [item for item in toc if item[0] == 1]
    for i, (level, title, page_num) in enumerate(top_level_chapters):
        start_page = page_num - 1
        if i + 1 < len(top_level_chapters):
            end_page = top_level_chapters[i+1][2] - 2
        else:
            end_page = len(doc) - 1
        chapters.append({"title": title, "start_page": start_page, "end_page": end_page})
    return chapters

def _get_chapters_by_heuristic(doc, title_font_size=18):
    """Finds chapter breaks by looking for large font sizes."""
    chapters = []
    potential_titles = []
    for i in range(len(doc)):
        blocks = doc.load_page(i).get_text("dict")["blocks"]
        for b in blocks:
            if "lines" in b:
                for l in b["lines"]:
                    for s in l["spans"]:
                        if s["size"] > title_font_size:
                            text = s["text"].strip()
                            if text and len(text) > 3:
                                potential_titles.append({"title": text, "page": i})
                                break
    
    if potential_titles:
        # Consolidate titles to form chapter ranges
        current_title = potential_titles[0]["title"]
        start_page = potential_titles[0]["page"]
        for i in range(1, len(potential_titles)):
            if potential_titles[i]["page"] > start_page:
                chapters.append({"title": current_title, "start_page": start_page, "end_page": potential_titles[i]["page"] - 1})
                current_title = potential_titles[i]["title"]
                start_page = potential_titles[i]["page"]
    chapters.append({"title": current_title, "start_page": start_page, "end_page": len(doc) - 1})
    return chapters

def parse_toc_with_ai(toc_text: str, total_pages: int, model: str = "gemini-2.5-pro"):
    """Sends a user-provided ToC to an AI to get structured chapter data."""
    print("Parsing Table of Contents with AI...")
    prompt = f'''
    You are a text-processing expert specializing in parsing book indexes and tables of contents (ToC).
    Your task is to analyze the following ToC text and return a valid JSON array of objects. Do not return any other text, just the JSON array.
    Each object in the array must represent a chapter and have exactly two keys:
    1.  `"title"`: The string name of the chapter.
    2.  `"page"`: The integer page number where the chapter starts.

    Here are some rules:
    - Ignore any introductory sections, prefaces, or content that comes before the first numbered or clearly defined chapter.
    - Chapter titles can be complex and contain numbers or punctuation.
    - Page numbers might be Roman numerals for introductory sections; these should be ignored.
    - The final JSON array should be sorted by the page number in ascending order.

    EXAMPLE INPUT:
    ----------------
    Contents
    Preface ... ix
    Chapter 1: The Beginning ... 1
    Chapter 2: The Middle ... 25
    Part II: The Plot Thickens
    Chapter 3: A New Challenge ... 50
    Index ... 100

    EXAMPLE OUTPUT:
    ----------------
    [
        {{
            "title": "Chapter 1: The Beginning",
            "page": 1
        }},
        {{
            "title": "Chapter 2: The Middle",
            "page": 25
        }},
        {{
            "title": "Chapter 3: A New Challenge",
            "page": 50
        }}
    ]

    Now, parse the following ToC text:
    ----------------
    {toc_text}
    '''
    response = generate_gemini(prompt, model=model)
    if not response or not response.text:
        raise ValueError("AI response was empty.")

    # Clean the response to get only the JSON part
    json_str = response.text.strip()
    if json_str.startswith('```json'):
        json_str = json_str[7:-3].strip()
    
    parsed_toc = json.loads(json_str)
    
    # Structure the data into chapters with start and end pages
    chapters = []
    for i, item in enumerate(parsed_toc):
        start_page = item['page']
        if i + 1 < len(parsed_toc):
            end_page = parsed_toc[i+1]['page'] - 1
        else:
            end_page = total_pages - 1 # Last chapter goes to the end
        # Adjust for 0-indexing and ensure valid ranges
        start_page_0_indexed = max(0, start_page - 1)
        end_page_0_indexed = max(start_page_0_indexed, end_page - 1)
        chapters.append({"title": item['title'], "start_page": start_page_0_indexed, "end_page": end_page_0_indexed})
    
    return chapters

def extract_pdf_chapters(path, strategy="auto", ocr=False, strategy_param=15):
    """
    Extracts text from a PDF, yielding it chunk by chunk based on the chosen strategy.
    This is a generator function.
    """
    doc = fitz.open(path)
    chapters = []

    # 1. Choose chapter detection strategy
    if strategy == "ai_toc":
        print("Using AI-Assisted ToC strategy...")
        toc_text = strategy_param
        if not isinstance(toc_text, str) or not toc_text.strip():
            raise ValueError("AI-Assisted strategy requires a non-empty ToC text.")
        chapters = parse_toc_with_ai(toc_text, len(doc))
    elif strategy == "auto":
        print("Using Auto-Detect strategy...")
        chapters = _get_chapters_from_toc(doc)
        if not chapters:
            # Use a default font size for auto-detect heuristic
            chapters = _get_chapters_by_heuristic(doc, title_font_size=18)
    elif strategy == "toc":
        # COMPELETE
        print("Using ToC strategy...")
    
    # 2. If no chapters found by chosen strategy (or for non-chapter strategies)
    if not chapters:
        if strategy in ["auto", "toc", "font"]:
             print("Could not determine chapters. Processing the whole document as one part.")
        # Use full document for these strategies or if they fail
        chapters.append({"title": os.path.basename(path), "start_page": 0, "end_page": len(doc) - 1})

    # 3. Yield text for each identified chapter/chunk
    if strategy == "page_chunks":
        chunk_size = strategy_param
        print(f"Using Fixed Page Chunks strategy (size: {chunk_size})...")
        for i in range(0, len(doc), chunk_size):
            start_page = i
            end_page = min(i + chunk_size - 1, len(doc) - 1)
            chunk_text = ""
            for page_num in range(start_page, end_page + 1):
                chunk_text += doc.load_page(page_num).get_text() + "\n"
                if ocr:
                    chunk_text += _get_ocr_text_for_page(doc, page_num)
            yield f"Pages {start_page + 1}-{end_page + 1}", chunk_text
    
    elif strategy == "custom_range":
        try:
            page_ranges = strategy_param
            if not isinstance(page_ranges, list):
                raise TypeError("strategy_param for custom_range must be a list of tuples.")
            
            for start_page, end_page in page_ranges:
                print(f"Using Custom Page Range strategy ({start_page + 1}-{end_page + 1})...")
                chunk_text = ""
                for page_num in range(start_page, end_page + 1):
                    if page_num < len(doc):
                        chunk_text += doc.load_page(page_num).get_text() + "\n"
                        if ocr:
                            chunk_text += _get_ocr_text_for_page(doc, page_num)
                yield f"Pages {start_page + 1}-{end_page + 1}", chunk_text
        except (TypeError, ValueError) as e:
            print(f"Error with custom range parameter: {e}.")
            # Yield nothing to avoid processing the whole doc on error
            return

    else: # For toc, font, auto, or full_document strategies
        for chapter in chapters:
            chapter_text = ""
            title = chapter['title']
            start_page = chapter['start_page']
            end_page = chapter['end_page']
            print(f"Extracting: '{title}' (Pages {start_page+1}-{end_page+1})")
            for i in range(start_page, end_page + 1):
                chapter_text += doc.load_page(i).get_text() + "\n"
                if ocr:
                    chapter_text += _get_ocr_text_for_page(doc, i)
            yield title, chapter_text

    doc.close()
