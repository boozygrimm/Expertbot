"""Build fixed-text trial sprites from the approved mockup (not used in production).

Developer-only dependencies: Pillow, numpy, opencv-python. Rendering the trial
uses only Pillow and the checked-in sprite sheet, not these dependencies.
"""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from video_prototype.sign_trial import MOCKUP, prepare_sign
from video_prototype.render import SIZE

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
LINES = ('ГРАВИТАЦИЯ', 'В ОТПУСКЕ')
OUTPUT = Path(__file__).with_name('typewriter.png')
W, H = 900, 330


def build():
    photo = Image.open(MOCKUP).convert('RGB')
    if photo.size != (2048, 2048):
        raise ValueError('Ожидался квадратный макет 2048 x 2048')
    font = ImageFont.truetype(FONT, 94)
    ends = [(0, 0)]
    for row, line in enumerate(LINES):
        for i, char in enumerate(line):
            if char != ' ':
                ends.append((row, i + 1))

    def mask(row, end):
        image = Image.new('L', (W, H))
        d = ImageDraw.Draw(image)
        for i, text in enumerate(LINES):
            if i > row:
                continue
            n = len(text) if i < row else end
            box = d.textbbox((0, 0), text, font=font, stroke_width=2)
            x = (W - (box[2] - box[0])) / 2 + (50 if i == 1 else 0)
            y = 22 + i * 125
            d.text((x + 3, y + 4), text[:n], font=font, fill=255, stroke_width=4, stroke_fill=255)
            d.text((x, y), text[:n], font=font, fill=255, stroke_width=2, stroke_fill=255)
        return np.array(image)

    matrix = cv2.getPerspectiveTransform(
        np.float32([[0,0],[W-1,0],[W-1,H-1],[0,H-1]]),
        np.float32([[126,397],[274,365],[288,438],[149,469]]) * (2048/768))
    def project(mask_data):
        # Cover anti-aliasing and the small shadow without revealing whole words.
        mask_data = cv2.dilate(mask_data, np.ones((11,11), np.uint8))
        result = cv2.warpPerspective(mask_data, matrix, photo.size, flags=cv2.INTER_LINEAR)
        return result

    original = np.array(photo)
    full_mask = project(mask(1, len(LINES[1])))
    blank = cv2.inpaint(original, full_mask, 5, cv2.INPAINT_TELEA)
    final_layer, position = prepare_sign()
    x, y = position
    tile_size = final_layer.size
    sheet = Image.new('RGBA', (tile_size[0] * len(ends), tile_size[1]))
    for index, (row, end) in enumerate(ends):
        visible = project(mask(row, end)).astype(np.float32) / 255
        pixels = (original * visible[:,:,None] + blank * (1 - visible[:,:,None])).astype(np.uint8)
        tile = Image.fromarray(pixels).resize((SIZE, SIZE), Image.Resampling.LANCZOS).crop((x, y, x+tile_size[0], y+tile_size[1]))
        tile.putalpha(final_layer.getchannel('A'))
        if index == len(ends) - 1:
            tile = final_layer  # exactly the approved static sign at the end
        sheet.paste(tile, (index * tile_size[0], 0))
    sheet.save(OUTPUT, optimize=True)
    print(f'{len(ends)} кадров, размер кадра {tile_size}, позиция {position}: {OUTPUT}')


if __name__ == '__main__':
    build()
