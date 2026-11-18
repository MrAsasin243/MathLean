# tools/install_assets.py (§8.4)
import os
import urllib.request
import zipfile
import io

KATEX_VERSION = "0.16.8"
KATEX_URL = f"https://github.com/KaTeX/KaTeX/releases/download/v{KATEX_VERSION}/katex.zip"

def main():
    print("=== Установка KaTeX assets ===")
    target_dir = os.path.join("static", "katex")
    os.makedirs(target_dir, exist_ok=True)
    
    print(f"Скачивание KaTeX v{KATEX_VERSION} с GitHub...")
    try:
        req = urllib.request.urlopen(KATEX_URL)
        with zipfile.ZipFile(io.BytesIO(req.read())) as z:
            z.extractall(target_dir)
        print(f"[OK] KaTeX успешно установлен в {target_dir}")
    except Exception as e:
        print(f"[ERROR] Не удалось скачать KaTeX: {e}")
        print("Вы можете скачать KaTeX вручную с https://github.com/KaTeX/KaTeX/releases и распаковать в static/katex/")

if __name__ == "__main__":
    main()
