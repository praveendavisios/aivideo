"""Builds real curriculum documents (PDF, scanned PDF, DOCX, PPTX, PNG) for tests."""
import os

CHAPTER = [
    ("h1", "Chapter 3: How Plants Make Food"),
    ("p", "Grade 5 Science"),
    ("h3", "Learning Objectives"),
    ("li", "Explain what photosynthesis is."),
    ("li", "Name the things a plant needs to make food."),
    ("h2", "3.1 Photosynthesis"),
    ("p", "Photosynthesis is the process by which green plants make their own food using sunlight, water and carbon dioxide."),
    ("p", "The green colour of leaves comes from chlorophyll. Chlorophyll is a green pigment that traps energy from sunlight."),
    ("p", "For example, a mango tree uses the sunlight falling on its leaves to make sugar."),
    ("h2", "3.2 Parts of a Leaf"),
    ("p", "Stomata are tiny openings on the underside of a leaf through which air enters and leaves."),
    ("p", "Water reaches the leaves from the roots through the stem."),
    ("h3", "Activity"),
    ("li", "Cover one leaf of a potted plant with black paper for three days and observe its colour."),
    ("h3", "Exercises"),
    ("li", "What do plants need to make food?"),
    ("p", "Answer: Sunlight, water and carbon dioxide."),
    ("li", "What is chlorophyll?"),
    ("p", "Answer: A green pigment in leaves that traps sunlight."),
]


def _text_lines():
    out = []
    for kind, t in CHAPTER:
        out.append(("- " + t) if kind == "li" else t)
    return out


def make_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    y = 800
    for i, line in enumerate(_text_lines()):
        if i == 9:  # page break before 3.2
            c.showPage()
            y = 800
        import textwrap
        for part in textwrap.wrap(line, 95) or [""]:
            c.setFont("Helvetica-Bold" if CHAPTER[i][0].startswith("h") else "Helvetica", 11)
            c.drawString(50, y, part)
            y -= 18
    c.save()
    return path


def render_page_image(lines, path, width=1700):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    img = Image.new("RGB", (width, 80 + 52 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((60, 40 + 52 * i), line, fill="black", font=font)
    img.save(path)
    return path


def make_scanned_pdf(path, tmpdir):
    """A PDF whose page is only an image (no text layer) — needs OCR."""
    from PIL import Image
    png = render_page_image(["Chapter 4: Water", "Evaporation is the process by which water changes into vapour.",
                             "What is evaporation?", "Answer: Water changing into vapour when heated."],
                            os.path.join(tmpdir, "scan.png"))
    Image.open(png).convert("RGB").save(path, "PDF", resolution=200)
    return path


def make_docx(path):
    import docx
    d = docx.Document()
    for kind, t in CHAPTER:
        if kind == "h1":
            d.add_heading(t, level=1)
        elif kind == "h2":
            d.add_heading(t, level=2)
        elif kind == "h3":
            d.add_heading(t, level=3)
        elif kind == "li":
            d.add_paragraph(t, style="List Bullet")
        else:
            d.add_paragraph(t)
    d.save(path)
    return path


def make_pptx(path):
    from pptx import Presentation
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Chapter 5: Magnets"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "5.1 Magnetic Poles"
    s.placeholders[1].text = "A magnet is an object that attracts iron.\nEvery magnet has a north pole and a south pole."
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Exercises"
    s.placeholders[1].text = "What does a magnet attract?\nAnswer: Iron."
    prs.save(path)
    return path
