"""Genera icon.ico (icono de la app) con Pillow, sin depender de Qt."""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
BG = (14, 14, 16, 255)
ACC = (224, 139, 62, 255)
ACC2 = (240, 160, 80, 255)

d.rounded_rectangle([S * 0.06, S * 0.06, S * 0.94, S * 0.94],
                    radius=int(S * 0.10), fill=BG, outline=ACC, width=max(2, S // 40))
cx = S / 2
sw = S * 0.14
d.rectangle([cx - sw / 2, S * 0.24, cx + sw / 2, S * 0.52], fill=ACC)               # tallo
d.polygon([(cx - S * 0.22, S * 0.5), (cx + S * 0.22, S * 0.5), (cx, S * 0.74)], fill=ACC)  # punta
d.rounded_rectangle([S * 0.26, S * 0.78, S * 0.74, S * 0.86],
                    radius=int(S * 0.03), fill=ACC2)                                 # bandeja

img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
img.save("icon.png")
print("icon.ico generado")
