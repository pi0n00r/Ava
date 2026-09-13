#!/usr/bin/env python3
"""Generate banner samples with mascot BEHIND text - "A" overlapping yellow circle."""

from PIL import Image, ImageDraw, ImageFont
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
mascot_path = os.path.join(project_root, "archived/AAVA-Mascots/aava.jpg")
assets_dir = os.path.join(project_root, "assets")

def remove_white_bg(img):
    img = img.convert("RGBA")
    datas = img.getdata()
    new_data = []
    for item in datas:
        if item[0] > 240 and item[1] > 240 and item[2] > 240:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)
    img.putdata(new_data)
    return img

# Load mascot from aava.jpg (the one without black ink on head)
mascot = Image.open(mascot_path)
mascot = remove_white_bg(mascot)

# Resize mascot (retina size)
mascot_height = 360
aspect = mascot.width / mascot.height
mascot_width = int(mascot_height * aspect)
mascot = mascot.resize((mascot_width, mascot_height), Image.Resampling.LANCZOS)

text = "Asterisk AI Voice Agent"
font_size = 120
font = None
font_paths = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf"
]
for font_name in font_paths:
    if os.path.exists(font_name):
        try:
            font = ImageFont.truetype(font_name, font_size, index=1 if "HelveticaNeue" in font_name else 0)
            break
        except: pass
if font is None: font = ImageFont.load_default()

temp_img = Image.new('RGBA', (1, 1))
temp_draw = ImageDraw.Draw(temp_img)
bbox = temp_draw.textbbox((0, 0), text, font=font)
text_width = bbox[2] - bbox[0]
text_height = bbox[3] - bbox[1]

padding_x = 40
padding_y = 40

def create_sample(name, bg_color, text_color):
    # CRITICAL: Large negative overlap so "A" is INSIDE the yellow circle
    # This means text_x starts BEFORE the mascot ends
    overlap = 180  # Pixels - "A" will be inside the yellow circle
    
    total_width = mascot_width + text_width - overlap + (padding_x * 2)
    total_height = max(mascot_height, text_height) + (padding_y * 2)
    
    banner = Image.new('RGBA', (total_width, total_height), bg_color)
    draw = ImageDraw.Draw(banner)
    
    mascot_x = padding_x
    mascot_y = (total_height - mascot_height) // 2
    
    # Text position: "A" should be INSIDE the yellow circle
    text_x = mascot_x + mascot_width - overlap
    # Align text lower, at the bottom portion of the mascot
    text_y = mascot_y + mascot_height - text_height - 40
    
    # Draw mascot FIRST, then text ON TOP (mascot behind text)
    banner.paste(mascot, (mascot_x, mascot_y), mascot)
    draw.text((text_x, text_y), text, font=font, fill=text_color)
    
    out_path = os.path.join(assets_dir, name)
    banner.save(out_path, 'PNG')
    print(f"Created {name}")

# Transparent backgrounds for seamless blending
create_sample("banner_dark_mode.png", (0, 0, 0, 0), (255, 255, 255, 255))  # White text for dark mode
create_sample("banner_light_mode.png", (0, 0, 0, 0), (36, 41, 47, 255))   # Dark gray text for light mode

