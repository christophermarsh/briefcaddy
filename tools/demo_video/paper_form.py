"""A made-up client who filled in the firm's paper questionnaire by hand:
one scanned-looking page (printed labels as on the firm's form, answers in a
handwriting font), and a typed I-94 that gives his name. Everything about him
is invented ("Exemplo"); the pipeline reads the page exactly as it reads a
real scan -- OCR to find the labels, the local vision model for the
handwriting.

    python tools/demo_video/paper_form.py <out folder> <handwriting .ttf>
"""

import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from portal.demo import document_pdf  # noqa: E402

PRINT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PRINT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
INK = (28, 46, 120)

# (printed label, handwritten answer); a None answer = a section heading
FORM = [
    ("INFORMAÇÕES PESSOAIS", None),
    ("Nome Completo:", "Lucas Exemplo Pereira"),
    ("Data de Nascimento:", "21/08/2007"),
    ("Cidade e País de Nascimento:", "Goiânia, Brasil"),
    ("Outros nomes já usados:", "Nenhum"),
    ("Endereço Físico:", "22 Example St Apt 3, Springfield MA 01104"),
    ("Data da última entrada nos Estados Unidos:", "05/03/2021"),
    ("Altura:", "5'8\""),
    ("Peso:", "150 lbs"),
    ("Cor do Cabelo:", "Preto"),
    ("Cor dos Olhos:", "Castanho"),
    ("MÃE:", None),
    ("Nome Completo:", "Rita Exemplo Pereira"),
    ("Nome de Solteira:", "Rita Exemplo Lima"),
    ("Data de Nascimento:", "14/02/1985"),
    ("País de Nascimento:", "Brasil"),
    ("PAI:", None),
    ("Nome Completo:", "Marcos Exemplo Pereira"),
    ("Data de Nascimento:", "30/09/1982"),
    ("País de Nascimento:", "Brasil"),
    ("Quantas vezes você já foi casado(a), incluindo o atual?", "Nenhuma (0)"),
    ("Quantos filhos você tem, incluindo fora dos EUA?", "Nenhum (0)"),
]

I94 = ["I-94/I-95 Official Website - Get Most Recent Response", "Most Recent I-94", "EXEMPLO -- DEMONSTRATION DOCUMENT",
       "Admission (I-94) Record Number: 99900045600", "Arrival/Issued Date: 2021 March 05", "Class of Admission: B2",
       "Admit Until Date: 09/04/2021", "Last/Surname: EXEMPLO PEREIRA", "First (Given) Name: LUCAS",
       "Birth Date: 08/21/2007", "Document Number: XX0004567", "Country of Citizenship: Brazil"]


def handwriting(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font: ImageFont.FreeTypeFont, rng: random.Random) -> None:
    """Word by word, each a little off the line and size, like a pen."""
    for word in text.split(" "):
        size = font.size + rng.randint(-2, 2)
        f = font.font_variant(size=size)
        draw.text((x, y + rng.randint(-3, 3)), word, font=f, fill=INK, stroke_width=1, stroke_fill=INK)  # a ballpoint, not a hairline
        x += int(draw.textlength(word + " ", font=f)) + rng.randint(-2, 3)


def page(hand_font: str) -> Image.Image:
    rng = random.Random(7)
    w, h = 1700, 2200  # letter at 200 dpi
    img = Image.new("RGB", (w, h), (250, 249, 245))
    d = ImageDraw.Draw(img)
    small, label, bold, title = (ImageFont.truetype(PRINT, 24), ImageFont.truetype(PRINT, 30),
                                 ImageFont.truetype(PRINT_BOLD, 32), ImageFont.truetype(PRINT_BOLD, 46))
    hand = ImageFont.truetype(hand_font, 54)
    d.text((120, 100), "Georges | Cote Law", font=small, fill=(60, 60, 60))
    d.text((120, 150), "Questionário para Ajuste de Status", font=title, fill=(20, 20, 20))
    d.text((120, 215), "I485 - SIJS", font=bold, fill=(20, 20, 20))
    d.line((120, 275, w - 120, 275), fill=(40, 40, 40), width=3)
    y = 320
    for text, answer in FORM:
        if answer is None:
            y += 18
            d.text((120, y), text, font=bold, fill=(20, 20, 20))
            y += 74
            continue
        d.text((120, y), text, font=label, fill=(25, 25, 25))
        x = 120 + int(d.textlength(text, font=label)) + 22
        d.line((x, y + 40, w - 120, y + 40), fill=(150, 150, 150), width=2)
        handwriting(d, x + 6, y - 26, answer, hand, rng)
        y += 78
    # a scan: slightly crooked, a little soft, faint grain
    img = img.rotate(-0.35, resample=Image.BICUBIC, fillcolor=(250, 249, 245))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    grain = Image.effect_noise((w, h), 9).convert("RGB")
    return Image.blend(img, grain, 0.04)


def main() -> None:
    out, hand_font = Path(sys.argv[1]).expanduser(), sys.argv[2]
    out.mkdir(parents=True, exist_ok=True)
    scan = page(hand_font)
    scan.save(out / "questionario.pdf", "PDF", resolution=200)
    scan.convert("RGB").resize((850, 1100)).save(out.parent / "questionario_preview.png")
    (out / "i94.pdf").write_bytes(document_pdf(I94))
    print(out)


if __name__ == "__main__":
    main()
