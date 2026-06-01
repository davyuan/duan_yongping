import re
import json
import sys
from pathlib import Path


def join_lines(lines):
    """Join lines; insert a space at ASCII word-boundary splits (e.g. 'must\\nbelieve')."""
    parts = [l.strip() for l in lines if l.strip()]
    if not parts:
        return ''
    result = parts[0]
    for part in parts[1:]:
        # Add a space only when both sides of the join are ASCII alphanumerics
        if (result and part
                and result[-1].isascii() and result[-1].isalnum()
                and part[0].isascii() and part[0].isalnum()):
            result += ' ' + part
        else:
            result += part
    return result


def remove_dates(text):
    """Remove date patterns like （2013-02-02） or (2013-02-02)."""
    text = re.sub(r'\s*[（(]\d{4}-\d{2}-\d{2}\s*[)）]\s*', '', text)
    return text.strip()


def parse_toc(lines):
    """Parse lines from the TOC section into sorted location entries."""
    entries = []
    current_chapter = None

    # TOC lines: "第一章投资理念............... 7"
    toc_re = re.compile(r'^(.+?)\s*\.{5,}\s*(\d+)\s*$')
    chapter_re = re.compile(r'^第[一二三四五六七八九十百千]+章\s*(.+)')
    section_re = re.compile(r'^第\s*\d+\s*节\s*(.+)')
    case_re    = re.compile(r'^案例\s*\d+\s*[：:]\s*(.+)')

    for raw in lines:
        line = raw.strip()
        if not line or line.isdigit():
            continue
        m = toc_re.match(line)
        if not m:
            continue

        title = m.group(1).strip().rstrip('.')
        page  = int(m.group(2))

        cm     = chapter_re.match(title)
        sm     = section_re.match(title)
        case_m = case_re.match(title)

        if cm:
            current_chapter = cm.group(1).strip()
            entries.append({'page': page, 'chapter': current_chapter, 'section': None})
        elif sm:
            entries.append({'page': page, 'chapter': current_chapter,
                            'section': sm.group(1).strip()})
        elif case_m:
            entries.append({'page': page, 'chapter': current_chapter,
                            'section': case_m.group(1).strip()})
        elif title in {'前言', '结尾', '附录'}:
            # Top-level structural sections — treat as chapter, reset section
            current_chapter = title
            entries.append({'page': page, 'chapter': current_chapter, 'section': None})
        else:
            # 目录, etc.
            entries.append({'page': page, 'chapter': current_chapter, 'section': title})

    return sorted(entries, key=lambda x: x['page'])


def get_location(page, toc_entries):
    """Return (chapter, section) for the given page number."""
    chapter = None
    section = None

    for entry in toc_entries:
        if entry['page'] > page:
            break
        if entry['chapter'] is not None:
            chapter = entry['chapter']
        if entry['section'] is not None:
            section = entry['section']
        elif entry['section'] is None and entry['chapter'] is not None:
            # New chapter header resets section
            section = None

    return chapter or '', section or ''


def parse_book(input_path, output_path):
    text  = Path(input_path).read_text(encoding='utf-8-sig', errors='ignore')
    lines = text.splitlines()

    # ── Locate and parse the Table of Contents ───────────────────────────────
    toc_start = toc_end = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if s == '目录':
            toc_start = i + 1
        elif toc_start is not None and toc_end is None:
            if s.isdigit():
                continue
            if s and not re.match(r'^.+\.{5,}\s*\d+\s*$', s):
                toc_end = i
                break

    toc_lines = lines[toc_start:toc_end] if toc_start is not None else []
    toc = parse_toc(toc_lines)
    print(f'TOC: {len(toc)} entries parsed')

    id_prefix = Path(input_path).stem

    # ── Regex patterns ────────────────────────────────────────────────────────
    page_re     = re.compile(r'^\d+$')
    question_re = re.compile(r'^(?:\d+\s*[.．]\s*)?(?:网友[\w\u4e00-\u9fff]*|问)\s*[：:]\s*(.*)$')
    answer_re   = re.compile(r'^段永平\s*[：:]\s*(.*)$')
    ch_hdr_re   = re.compile(r'^第[一二三四五六七八九十百]+章')
    sec_hdr_re  = re.compile(r'^第\s*\d+\s*节')
    case_hdr_re = re.compile(r'^案例\s*\d+\s*[：:]')
    special_hdr = {'前言', '结尾', '附录', '目录'}
    qanda_re    = re.compile(r'^问答\s*[：:]?\s*$')    # Numbered items whose text should NOT be treated as questions (citations, blog posts, etc.)
    yinyong_re        = re.compile(r'^\d+\s*[.．]\s*引用\s*[：:]\s*(.*)')
    non_q_numbered_re = re.compile(
        r'^(?:博文\s*[：:.]|总结\s*[：:]?|小案例\s*[：:]|【引用|乐趣\s*引用|[A-Z]{2,3}\s*[：:])'
    )
    # Indicators that a numbered item (without 网友:) is a question directed at 段永平
    q_indicator_re    = re.compile(r'[？您]|请[问教]|想[问请]|大道')
    # 2-digit-or-more numbered item without 网友: prefix ("08.大道好，..." style)
    numbered_noalias_re = re.compile(r'^\d{2,}\s*[.．]\s*(.+)$')
    # ── State machine ─────────────────────────────────────────────────────────
    results  = []
    counter  = 0
    cur_page = 1
    qa_page  = 1
    q_buf    = []   # lines of current question
    a_buf    = []   # lines of current answer segment
    answers  = []   # finalized answer segments for current question
    state    = 'outside'  # outside | sec_intro | q | a
    sec_intro_title    = None
    sec_intro_buf      = []  # current segment being accumulated
    sec_intro_segments = []  # completed segments
    intro_page         = 1

    def flush_answer():
        if a_buf:
            t = remove_dates(join_lines(a_buf))
            if t:
                answers.append(t)
            a_buf.clear()

    def flush_sec_intro_segment():
        if sec_intro_buf:
            t = remove_dates(join_lines(sec_intro_buf))
            if t:
                sec_intro_segments.append(t)
            sec_intro_buf.clear()

    def flush_sec_intro():
        nonlocal counter, sec_intro_title
        flush_sec_intro_segment()
        if sec_intro_title and sec_intro_segments:
            a = '\n'.join(sec_intro_segments)
            counter += 1
            ch, sec = get_location(intro_page, toc)
            results.append({
                'id': f'{id_prefix}_{counter:03d}',
                'text': f'Question: {sec_intro_title} Answer: {a}',
                'metadata': {
                    'question': sec_intro_title,
                    'chapter':  ch,
                    'section':  sec,
                    'page':     intro_page,
                },
            })
        sec_intro_title = None
        sec_intro_buf.clear()
        sec_intro_segments.clear()

    def flush_qa():
        nonlocal counter
        flush_answer()
        if q_buf and answers:
            counter += 1
            q  = remove_dates(join_lines(q_buf))
            a  = '\n'.join(answers)
            ch, sec = get_location(qa_page, toc)
            results.append({
                'id': f'{id_prefix}_{counter:03d}',
                'text': f'Question: {q} Answer: {a}',
                'metadata': {
                    'question': q,
                    'chapter':  ch,
                    'section':  sec,
                    'page':     qa_page,
                },
            })
        q_buf.clear()
        answers.clear()

    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        # Page marker
        if page_re.match(s) and s.isdigit():
            cur_page = int(s)
            continue

        # Separator lines (e.g. "------...") — skip entirely
        if re.match(r'^[-─—=*]{4,}$', s):
            continue

        # 问答: marker — flush section intro (if any) then reset
        if qanda_re.match(s):
            flush_sec_intro()
            flush_qa()
            state = 'outside'
            continue

        # Section header → flush pending Q&A + intro, start accumulating section intro
        sm = sec_hdr_re.match(s)
        if sm:
            flush_sec_intro()
            flush_qa()
            # Extract the section title (text after 第N节)
            sec_intro_title = re.sub(r'^第\s*\d+\s*节\s*', '', s).strip()
            intro_page = cur_page
            sec_intro_buf.clear()
            sec_intro_segments.clear()
            state = 'sec_intro'
            continue

        # Chapter / case / special headers → flush everything, reset state
        if (ch_hdr_re.match(s) or case_hdr_re.match(s) or s in special_hdr):
            flush_sec_intro()
            flush_qa()
            state = 'outside'
            continue

        # Question start
        qm = question_re.match(s)
        if qm:
            if state == 'sec_intro':
                flush_sec_intro()
            flush_qa()
            qa_page = cur_page
            q_buf.clear()
            first = qm.group(1).strip()
            if first:
                q_buf.append(first)
            state = 'q'
            continue

        # Numbered 引用: (quoted reference for comment) → start new Q&A
        ym = yinyong_re.match(s)
        if ym:
            if state == 'sec_intro':
                flush_sec_intro()
            flush_qa()
            qa_page = cur_page
            q_buf.clear()
            first = ym.group(1).strip()
            if first:
                q_buf.append(first)
            state = 'q'
            continue

        # Numbered question without 网友: prefix (e.g. "08.大道好，..." or "06.这么多手机公司，为什么苹果最成功？")
        # Uses 2+ digit requirement to avoid treating list bullets inside answers as new questions.
        nm = numbered_noalias_re.match(s)
        if nm:
            text = nm.group(1).strip()
            if not non_q_numbered_re.match(text) and q_indicator_re.search(text):
                if state == 'sec_intro':
                    flush_sec_intro()
                flush_qa()
                qa_page = cur_page
                q_buf.clear()
                q_buf.append(text)
                state = 'q'
                continue

        # Answer start
        am = answer_re.match(s)
        if am:
            if state == 'sec_intro':
                # 段永平: within section intro — keep as intro content, don't start a new Q&A
                first = am.group(1).strip()
                if first:
                    sec_intro_buf.append(first)
                    if re.search(r'[（(]\d{4}[-\u2013]\d{2}[-\u2013]\d{2}\s*[)）]\s*$', first):
                        flush_sec_intro_segment()
                continue
            if state == 'a':
                flush_answer()
            a_buf.clear()
            first = am.group(1).strip()
            if first:
                a_buf.append(first)
                if re.search(r'[（(]\d{4}[-–]\d{2}[-–]\d{2}[)）]\s*$', first):
                    flush_answer()
            state = 'a'
            continue

        # Continuation lines
        if state == 'q':
            q_buf.append(s)
        elif state == 'a':
            a_buf.append(s)
            # A line ending with a date closes this timed segment
            if re.search(r'[（(]\d{4}[-–]\d{2}[-–]\d{2}[)）]\s*$', s):
                flush_answer()
        elif state == 'sec_intro':
            sec_intro_buf.append(s)
            # A line ending with a date closes this timed segment
            if re.search(r'[\uff08(]\d{4}[-\u2013]\d{2}[-\u2013]\d{2}[)\uff09]\s*$', s):
                flush_sec_intro_segment()
        # state == 'outside': skip

    flush_sec_intro()
    flush_qa()

    # ── Write output ──────────────────────────────────────────────────────────
    Path(output_path).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    print(f'Written {len(results)} Q&A entries → {output_path}')


if __name__ == '__main__':
    inp = sys.argv[1] if len(sys.argv) > 1 else 'duan2.txt'
    out = sys.argv[2] if len(sys.argv) > 2 else str(Path(inp).with_suffix('.json'))
    parse_book(inp, out)
