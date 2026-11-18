# tools/install_lean.py (§3)
import sys
import subprocess
import shutil
import os

def check_lean() -> bool:
    lean_home = os.environ.get("MP_LEAN_HOME")
    if lean_home and os.path.exists(os.path.join(lean_home, "bin", "lean")):
        return True
    return shutil.which("lean") is not None and shutil.which("lake") is not None

def main():
    is_check = "--check" in sys.argv
    print("=== Проверка установки Lean 4 (elan / lean) ===")
    
    if check_lean():
        print("[OK] Lean 4 и Lake обнаружены в системе.")
        sys.exit(0)
    else:
        print("[!] Lean 4 или Lake не найдены в PATH.")
        if is_check:
            sys.exit(1)
            
        print("\nИнструкция по установке:")
        print("  Windows: установите elan с помощью официального установщика leanprover/elan.")
        print("  Linux/macOS: выполните в терминале:")
        print("    curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh")
        print("\nПосле установки перезапустите терминал и выполните: lake build в каталоге lean/")
        sys.exit(1)

if __name__ == "__main__":
    main()
