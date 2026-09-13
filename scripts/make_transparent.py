#!/usr/bin/env python3
from PIL import Image
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
mascot_path = os.path.join(project_root, "archived/AAVA-Mascots/aava.jpg")
output_path = os.path.join(project_root, "assets/mascot_transparent.png")

def remove_white_bg(img):
    img = img.convert("RGBA")
    datas = img.getdata()
    new_data = []
    # Make background transparent if it's white/light gray
    for item in datas:
        if item[0] > 240 and item[1] > 240 and item[2] > 240:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)
    img.putdata(new_data)
    return img

mascot = Image.open(mascot_path)
mascot = remove_white_bg(mascot)
# Trim transparent edges
bbox = mascot.getbbox()
if bbox:
    mascot = mascot.crop(bbox)

mascot.save(output_path, 'PNG')
print(f"Saved transparent mascot to {output_path}")
