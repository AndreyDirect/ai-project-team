from pathlib import Path

ROOT = Path(__file__).parent

MODEL = "openai/gpt-oss-120b"

BRIEF_FILE = ROOT / "project_brief.txt"
OUTPUT_DIR = ROOT / "output"

RETRY_DELAYS = [5, 15, 45]
TEMPERATURE = 0.3