from pathlib import Path
import re
from collections import Counter
root = Path('projects/nature-protection-students_ppt169_20260825/svg_output')
counts = Counter()
for path in sorted(root.glob('*.svg')):
    text = path.read_text(encoding='utf-8')
    counts.update(re.findall(r'font-size="([0-9]+)"', text))
for size, count in sorted(counts.items(), key=lambda item: int(item[0])):
    if count > 2:
        print(f'{size}: {count}')
