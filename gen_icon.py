"""Generate Claude-style sparkle icon as .ico file."""
from PIL import Image, ImageDraw, ImageFont
import math

def create_claude_icon():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []

    for size in sizes:
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Dark circle background
        pad = max(1, size // 16)
        draw.ellipse([pad, pad, size - pad - 1, size - pad - 1], fill=(15, 14, 23, 255))

        cx, cy = size / 2, size / 2

        # Claude's coral/orange color
        color = (217, 119, 87, 255)  # Claude's signature coral

        # Draw a sparkle/asterisk shape (Claude's logo is a stylized asterisk)
        # 6 pointed rays with rounded ends
        num_rays = 6
        ray_length = size * 0.32
        ray_width = max(2, size * 0.09)
        dot_r = max(1.5, size * 0.055)

        for i in range(num_rays):
            angle = math.radians(i * 60 - 90)  # Start from top
            ex = cx + ray_length * math.cos(angle)
            ey = cy + ray_length * math.sin(angle)

            # Draw ray line
            draw.line([(cx, cy), (ex, ey)], fill=color, width=max(2, int(ray_width)))

            # Draw dot at end of ray
            draw.ellipse([ex - dot_r, ey - dot_r, ex + dot_r, ey + dot_r], fill=color)

        # Center dot (slightly larger)
        center_r = max(2, size * 0.08)
        draw.ellipse([cx - center_r, cy - center_r, cx + center_r, cy + center_r], fill=color)

        images.append(img)

    # Save as .ico with multiple sizes
    images[0].save('claude-icon.ico', format='ICO',
                   sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"Created claude-icon.ico with sizes: {sizes}")

if __name__ == "__main__":
    create_claude_icon()
