"""Prepare an optional, bounded typewriter sign on the approved scene."""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont

if __package__:
    from .sign_text import normalize_sign
    from .render import SIZE
else:
    from sign_text import normalize_sign
    from render import SIZE

HERE = Path(__file__).resolve().parent
FONT = HERE / 'assets/sign-font.ttf'
BLANK = HERE / 'assets/sign-blank.png'
TILE_WIDTH, TILE_HEIGHT = 102, 60
POSITION = (79, 228)
DELAY, INTERVAL = .3, .1
WIDTH, HEIGHT = 900, 330


def _perspective_coefficients(destination, source):
    """Pillow needs output-to-input projective coefficients."""
    rows = []
    for (x, y), (u, v) in zip(destination, source):
        rows.extend([[x,y,1,0,0,0,-u*x,-u*y,u],
                     [0,0,0,x,y,1,-v*x,-v*y,v]])
    for col in range(8):
        pivot = max(range(col, 8), key=lambda i: abs(rows[i][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        factor = rows[col][col]
        if abs(factor) < 1e-12:
            raise ValueError('Некорректная перспектива таблички')
        rows[col] = [x/factor for x in rows[col]]
        for i in range(8):
            if i != col:
                factor = rows[i][col]
                rows[i] = [a-factor*b for a,b in zip(rows[i], rows[col])]
    return tuple(row[-1] for row in rows)


def _layout(text):
    words = text.split()
    candidates = [tuple([' '.join(words)])] if len(words) == 1 else [
        (' '.join(words[:i]), ' '.join(words[i:])) for i in range(1, len(words))]
    measure = ImageDraw.Draw(Image.new('L', (1,1)))
    for size in range(94, 59, -2):
        font = ImageFont.truetype(str(FONT), size)
        viable = []
        for lines in candidates:
            widths = [measure.textbbox((0,0), line, font=font, stroke_width=4)[2] for line in lines]
            if all(w <= (WIDTH - 100 if i == 0 else WIDTH - 150) for i, w in enumerate(widths)):
                viable.append((max(widths), abs(widths[0] - widths[-1]), lines))
        if viable:
            return font, min(viable)[2]
    raise ValueError('Надпись не помещается на табличку')


def prepare(text):
    """Produce transparent static layers once per clip, not once per frame."""
    text = normalize_sign(text)
    if text is None:
        raise ValueError('Некорректная надпись')
    font, lines = _layout(text)
    with Image.open(BLANK) as source:
        if source.size != (TILE_WIDTH, TILE_HEIGHT):
            raise ValueError('Не найдена подходящая основа таблички')
        blank = source.convert('RGBA')
    scale = SIZE * 2 / 768
    dest = [(x*scale, y*scale) for x,y in [(126,397),(274,365),(288,438),(149,469)]]
    coeff = _perspective_coefficients(dest, [(0,0),(WIDTH-1,0),(WIDTH-1,HEIGHT-1),(0,HEIGHT-1)])
    total = sum(sum(c != ' ' for c in line) for line in lines)
    result = [blank]
    for visible_count in range(1, total + 1):
        text_img = Image.new('RGBA', (WIDTH, HEIGHT))
        draw = ImageDraw.Draw(text_img)
        remaining = visible_count
        for i, line in enumerate(lines):
            count = 0
            for char in line:
                if char != ' ':
                    if remaining <= 0:
                        break
                    remaining -= 1
                count += 1
            bbox = draw.textbbox((0,0), line, font=font, stroke_width=2)
            x = (WIDTH - (bbox[2]-bbox[0]))/2 + (50 if i == 1 else 0)
            y = (90 if len(lines) == 1 else 22 + i*125)
            part = line[:count]
            draw.text((x+3,y+4), part, font=font, fill=(15,5,3), stroke_width=4, stroke_fill=(15,5,3))
            draw.text((x,y), part, font=font, fill=(255,222,136), stroke_width=2, stroke_fill=(45,16,7))
        projected = text_img.transform((SIZE*2, SIZE*2), Image.Transform.PERSPECTIVE, coeff,
                                       resample=Image.Resampling.BICUBIC)
        projected = projected.resize((SIZE,SIZE), Image.Resampling.LANCZOS)
        x,y = POSITION
        projected = projected.crop((x,y,x+TILE_WIDTH,y+TILE_HEIGHT))
        projected.putalpha(ImageChops.multiply(projected.getchannel('A'), blank.getchannel('A')))
        result.append(Image.alpha_composite(blank, projected))
    return result, POSITION


def index_at(seconds, count, duration=None):
    if count <= 1:
        return 0
    delay, interval = DELAY, INTERVAL
    if duration is not None:
        # Short clips must still reach their final text by the last frame.
        delay = min(DELAY, max(0., duration * .15))
        available = max(1e-6, duration - 1/24 - delay)
        interval = min(INTERVAL, available / (count - 1))
    return min(count - 1, int(max(0., seconds - delay) / interval + 1e-8))


def apply(frame, layer, position):
    image = Image.frombytes('RGB', (SIZE, SIZE), frame)
    image.paste(layer, position, layer)
    return image.tobytes()
