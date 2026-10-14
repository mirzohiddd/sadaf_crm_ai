"""SADAF CRM backend: noto'g'ri nomlangan fayllarni to'g'ri joyiga qo'yadi.

Muammo: yangilangan fayllar "app__services__arrival (1).py" kabi nomlar bilan
loyihaga tushgan. Python modul nomi faqat "arrival.py" bo'lishi kerak, shuning
uchun `from ..services import arrival` ImportError beradi.

Ishlatish (backend papkasi ichida):
    python3 fix_backend_files.py           # tuzatadi
    python3 fix_backend_files.py --check   # faqat tekshiradi, hech narsani o'zgartirmaydi

Skript TEGMAYDI: .env, data/ (json, recordings), .venv, .git.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "venv", "data", "node_modules", "__pycache__", ".pytest_cache"}
PROTECTED = {".env"}

# Yuborilgan fayl nomi (" (1)" qo'shimchasisiz) -> loyihadagi haqiqiy joyi
TARGETS = {
    "app__services__arrival.py": "app/services/arrival.py",
    "app__services__baza.py": "app/services/baza.py",
    "app__routers__baza.py": "app/routers/baza.py",
    "app__routers__leads.py": "app/routers/leads.py",
    "app__routers__integrations.py": "app/routers/integrations.py",
    "app__config.py": "app/config.py",
    "app__seed.py": "app/seed.py",
    "tests__test_baza_and_dates.py": "tests/test_baza_and_dates.py",
    "tests__test_sheets_import.py": "tests/test_sheets_import.py",
    "env.example": ".env.example",
    # Oddiy nomli fayllar faqat " (1)" kabi nusxa bo'lib tushgan bo'lsa ko'chiriladi
    "main.py": "main.py",
    "requirements.txt": "requirements.txt",
    "README.md": "README.md",
}

# Har bir fayl yangi versiya ekanini tasdiqlovchi belgilar
MARKERS = {
    "app/services/arrival.py": "def arrival_day",
    "app/services/baza.py": "def list_leads",
    "app/routers/baza.py": 'prefix="/api/leads/baza"',
    "app/routers/leads.py": "def parse_range",
    "app/routers/integrations.py": "from ..services import arrival, assignment, notify",
    "app/config.py": "DATABASE_URL",
    "app/seed.py": "def backfill_lead_arrivals",
    "main.py": "baza.router",
    "requirements.txt": "psycopg",
    "tests/test_baza_and_dates.py": "def test_baza_auto_creates_table",
    "tests/test_sheets_import.py": '"17:39"',
    ".env.example": "DATABASE_URL",
}

def clean_name(name: str) -> str:
    return re.sub(r" \(\d+\)(?=(\.[^.]+)?$)", "", name)


def find_misnamed() -> list[tuple[Path, Path]]:
    moves: list[tuple[Path, Path]] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            src = Path(dirpath) / fname
            if fname in PROTECTED or src == Path(__file__).resolve():
                continue
            key = clean_name(fname)
            if key not in TARGETS:
                continue
            dst = ROOT / TARGETS[key]
            if src.resolve() == dst.resolve():
                continue  # allaqachon to'g'ri joyida
            if key == fname and "__" not in fname and key != "env.example":
                continue  # oddiy nom, boshqa papkada — tegmaymiz (masalan app/main.py emas)
            moves.append((src, dst))
    # Bir nechta nusxa bo'lsa — eng yangisini (oxirgi o'zgartirilganini) olamiz
    best: dict[Path, Path] = {}
    for src, dst in moves:
        if dst not in best or src.stat().st_mtime > best[dst].stat().st_mtime:
            best[dst] = src
    return [(src, dst) for dst, src in best.items()] + [
        (src, None) for src, dst in moves if best.get(dst) != src  # ortiqcha nusxalar
    ]


def verify() -> list[str]:
    problems = []
    for rel, marker in MARKERS.items():
        path = ROOT / rel
        if not path.exists():
            problems.append(f"YO'Q: {rel}")
        elif marker not in path.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"ESKI VERSIYA: {rel} (yangilangan faylni qo'ying)")
    leftovers = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
                 if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS)
                 and ("__" in p.stem.strip("_") and p.suffix in (".py", ".md", ".txt")
                      or re.search(r" \(\d+\)\.", p.name))]
    problems += [f"ORTIQCHA FAYL: {x}" for x in leftovers]
    return problems


def import_check() -> bool:
    env = dict(os.environ, DATA_DIR=tempfile.mkdtemp(prefix="sadaf-check-"))
    code = "import main; print('OK: main.py import qilindi, routerlar:', len(main.app.routes))"
    res = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    print((res.stdout or res.stderr).strip().splitlines()[-1] if (res.stdout or res.stderr) else "")
    return res.returncode == 0


def main() -> int:
    check_only = "--check" in sys.argv
    print(f"Backend papkasi: {ROOT}")
    if not (ROOT / "main.py").exists() or not (ROOT / "app").is_dir():
        print("XATO: skriptni backend papkasi ichida ishga tushiring (main.py va app/ shu yerda bo'lishi kerak).")
        return 2

    for src, dst in find_misnamed():
        rel = src.relative_to(ROOT)
        if dst is None:
            print(f"  o'chiriladi (ortiqcha nusxa): {rel}")
            if not check_only:
                src.unlink()
            continue
        print(f"  {rel}  ->  {dst.relative_to(ROOT)}")
        if not check_only:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    for cache in ROOT.rglob("__pycache__"):
        if not (set(cache.relative_to(ROOT).parts) & {".venv", "venv", ".git"}) and not check_only:
            shutil.rmtree(cache, ignore_errors=True)

    problems = verify()
    print("\nTekshiruv:")
    for p in problems:
        print("  -", p)
    if not problems:
        print("  barcha fayllar to'g'ri nomda va yangi versiyada")
    ok = import_check()
    return 0 if ok and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
