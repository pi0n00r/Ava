#!/usr/bin/env python3
"""Create dynamic high-resolution banner image."""

from PIL import Image, ImageDraw, ImageFont
import os

# Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
mascot_path = os.path.join(project_root, "archived/AAVA-Mascots/aava.jpg")
output_path = os.path.join(project_root, "assets/banner.png")

def remove_white_bg(img):
    """Remove white background from image smoothly."""
    img = img.convert("RGBA")
    datas = img.getdata()
    new_data = []
    for item in datas:
        # If pixel is close to white, make it transparent
        if item[0] > 240 and item[1] > 240 and item[2] > 240:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)
    img.putdata(new_data)
    return img

# Load and process mascot
mascot = Image.open(mascot_path)
mascot = remove_white_bg(mascot)

# Resize mascot (make it large for crisp retina display)
mascot_height = 360
aspect = mascot.width / mascot.height
mascot_width = int(mascot_height * aspect)
mascot = mascot.resize((mascot_width, mascot_height), Image.Resampling.LANCZOS)

# Setup text
text = "Asterisk AI Voice Agent"
TEXT_COLOR = (255, 255, 255)  # White text

# Use the best available bold font for Mac
font_size = 140
font = None
font_paths = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial.ttf"
]

for font_name in font_paths:
    if os.path.exists(font_name):
        try:
            # Try to grab the Bold variant if it's a TTC
            font = ImageFont.truetype(font_name, font_size, index=1 if "HelveticaNeue" in font_name else 0)
            break
        except:
            pass

if font is None:
    font = ImageFont.load_default()

# Get text dimensions using a temporary image
temp_img = Image.new('RGBA', (1, 1))
temp_draw = ImageDraw.Draw(temp_img)
bbox = temp_draw.textbbox((0, 0), text, font=font)
text_width = bbox[2] - bbox[0]
text_height = bbox[3] - bbox[1]

# Layout parameters
overlap = 70  # Pixels of overlap
padding_x = 40
padding_y = 40

# Calculate exact required image dimensions to prevent cutoff
total_width = mascot_width + text_width - overlap + (padding_x * 2)
total_height = max(mascot_height, text_height) + (padding_y * 2)

# Create perfectly sized transparent background
banner = Image.new('RGBA', (total_width, total_height), (0, 0, 0, 0))
draw = ImageDraw.Draw(banner)

# Calculate positions
mascot_x = padding_x
mascot_y = (total_height - mascot_height) // 2

text_x = mascot_x + mascot_width - overlap
# Align text slightly lower to overlap the hand/circle area smoothly like the snapshot
text_y = mascot_y + mascot_height - text_height - 60

# Draw mascot FIRST
banner.paste(mascot, (mascot_x, mascot_y), mascot)

# Draw text OVER the mascot
draw.text((text_x, text_y), text, font=font, fill=TEXT_COLOR)

# Save banner
banner.save(output_path, 'PNG')
print(f"Banner created: {output_path} (Size: {total_width}x{total_height})")
