"""Port of the .ps1 classification/rename logic. Regex-driven, compact.

Two entry points:
  - detect_assessment(pdf_text, filename) -> str | None
  - detect_name(pdf_text, filename)       -> (first, last) | None
"""
import re

# Assessment type detection from PDF text. Order matters: Spanish before English.
# ponytail: `.{1,3}` handles ligatures ("Proﬁler") — same trick the .ps1 uses.
_ASSESSMENT_TEXT_PATTERNS = [
    (r"Per.{1,3}l\s+de\s+intereses\s+O[\*\s]?NET.*Lista de carreras realistas", "O_NET_Perfil_de_intereses_Lista_Carreras_Realistas"),
    (r"Per.{1,3}l\s+de\s+intereses\s+O[\*\s]?NET.*Lista de carreras sociales",  "O_NET_Perfil_de_intereses_Lista_Carreras_Sociales"),
    (r"Per.{1,3}l\s+de\s+intereses\s+O[\*\s]?NET.*Lista de carreras",           "O_NET_Perfil_de_intereses_Lista_Carreras"),
    (r"Per.{1,3}l\s+de\s+intereses\s+O[\*\s]?NET.*Zonas de Trabajo",            "O_NET_Perfil_de_intereses_Zonas_Trabajo"),
    (r"Per.{1,3}l\s+de\s+intereses\s+O[\*\s]?NET",                              "O_NET_Perfil_de_intereses"),
    (r"O[\*\s]?NET\s+Interest\s+Pro.{1,3}ler.*Realistic\s+Career\s+List",       "O_NET_Interest_Profiler_Realistic_Career_List"),
    (r"O[\*\s]?NET\s+Interest\s+Pro.{1,3}ler.*Career\s+List",                   "O_NET_Interest_Profiler_Career_List"),
    (r"O[\*\s]?NET\s+Interest\s+Pro.{1,3}ler.*Score\s+Report",                  "O_NET_Interest_Profiler_Score_Report"),
    (r"O[\*\s]?NET\s+Interest\s+Pro.{1,3}ler",                                  "O_NET_Interest_Profiler"),
    (r"VIA Character Strengths",                                                "VIA_Character_Strengths_Profile"),
    (r"StrengthsProfile",                                                       "StrengthsProfile"),
]

# Fallback: infer generic type from filename when PDF text yields nothing.
_ASSESSMENT_FILENAME_FALLBACKS = [
    (r"O_NET|O\*NET|ONET|O\s+NET", "O_NET_Interest_Profiler"),
    (r"Perfil de intereses",       "O_NET_Perfil_de_intereses"),
    (r"VIA|StrengthsProfile",      "VIA_Character_Strengths_Profile"),
]


def detect_assessment(pdf_text: str | None, filename: str) -> str | None:
    if pdf_text:
        for pat, result in _ASSESSMENT_TEXT_PATTERNS:
            if re.search(pat, pdf_text, re.DOTALL):
                return result
    for pat, result in _ASSESSMENT_FILENAME_FALLBACKS:
        if re.search(pat, filename):
            return result
    return None


# --- Name detection ---

_EXCLUDE = {
    # English
    "INTEREST", "PROFILER", "CAREER", "LIST", "SCORE", "REPORT", "CHARACTER",
    "STRENGTHS", "SURVEY", "RESULTS", "INSTITUTE", "REALISTIC", "SOCIAL",
    "SUPPORT", "WORK", "FREE", "JOB", "ZONE", "PREPARATION", "NECESSARY",
    "MEDIUM", "NEEDED", "LITTLE", "SOME", "NEXT", "MOVE", "PRINTED", "FOR",
    "PAGE", "DOCUMENT", "FILE", "PDF", "ASSESSMENT", "TEST", "EXAM",
    "INVESTIGATIVE", "ARTISTIC", "CONVENTIONAL", "ENTERPRISING",
    "ACTIVITIES", "SKILLS", "ABILITIES", "MY", "AT", "THE", "NET",
    # Spanish
    "LISTA", "CARRERAS", "ZONAS", "TRABAJO", "EN", "MI", "DE", "PASO",
    "PROXIMO", "PRÓXIMO", "XIMO", "PERFIL", "INTERESES", "COPIA", "IMPRESA",
    "PARA", "ENCUESTA", "FORTALEZAS", "SOCIALES", "REALISTAS",
}

_NAME_TOKEN = r"[A-Za-z]+(?:[-'][A-Za-z]+)?"


def _clean(s: str) -> str:
    return s.strip()


def _title(first: str, last: str) -> tuple[str, str]:
    return first.strip().title(), last.strip().title()


def _from_pdf_text(text: str, is_via: bool) -> tuple[str, str] | None:
    # Pattern 1: "Printed for: ..." / "Copia impresa para: ..."
    m = re.search(
        rf"(?:Printed for|Copia impresa para):\s*({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*?)\s+({_NAME_TOKEN}(?:\s+(?:Jr\.?|Sr\.?|III?|IV))?)",
        text,
    )
    if m:
        return _title(m.group(1), m.group(2))

    # Pattern 2: "Name: ..." / "Nombre: ..."
    m = re.search(
        rf"(?:Name|Nombre):\s*({_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*?)\s+({_NAME_TOKEN})",
        text,
    )
    if m:
        return _title(m.group(1), m.group(2))

    # Pattern 3: All-caps 2-3 word line near top of doc. VIA/StrengthsProfile only.
    if not is_via:
        return None
    for line in text.splitlines()[:10]:
        line = _clean(line)
        for pat in (
            r"^([A-Z]+(?:[-'][A-Z]+)*)\s+([A-Z]+(?:[-'][A-Z]+)*)\s+([A-Z]+(?:[-'][A-Z]+)*)$",
            r"^([A-Z]+(?:[-'][A-Z]+)*)\s+([A-Z]+(?:[-'][A-Z]+)*)$",
        ):
            m = re.match(pat, line)
            if m:
                parts = [p.upper() for p in m.groups()]
                if not any(p in _EXCLUDE for p in parts):
                    return _title(parts[0], parts[-1])
    return None


# Filename patterns, tried in order.
_FILENAME_PATTERNS = [
    r"^([A-Za-z]+)\s+([A-Za-z]+)\s+(?:VIA|O_NET|Perfil|StrengthsProfile)",
    r"^([A-Z][a-z]+)\s+([A-Z][a-z]+)(?:O_NET|VIA|Perfil)",
    r"StrengthsProfile-([A-Za-z]+(?:-[A-Za-z]+)?)-([A-Za-z]+)",
    r"\[([A-Za-z]+)\s+([A-Za-z]+)\]",
    r"-\s*([A-Za-z]+)\s+([A-Za-z]+)(?:\s|$)",
    r"O_NET[_\s]+([A-Za-z]+)[_\s]+([A-Za-z]+)",
    r"Paso([a-z]+)\s+([a-z]+)",
    r"Move\s+([A-Z][a-z]+)\s+([A-Z][a-z]+)",
    r"Move([A-Z][a-z]+)([A-Z][a-z]+)",
    r"^([A-Za-z]+)\s+([A-Za-z]+)\s+ONET",
    r"O\s+NET\s+Interest\s+Profile-([A-Za-z]+)\s+([A-Za-z]+)",
    r"-([A-Za-z]+)\s+([A-Za-z]+),\s*(?:Jr\.?|Sr\.?|III?|IV)",
    r"\s([A-Z][a-z]+)\s+([A-Z][a-z]+)$",
    # ponytail: matches this app's own committed output (Assessment-First_Last.pdf).
    # Placed last so more specific patterns take precedence; anchored at end so
    # it doesn't grab the assessment tokens (Interest_Profiler, etc.).
    r"-([A-Za-z]+(?:-[A-Za-z]+)?)_([A-Za-z]+(?:-[A-Za-z]+)?)$",
]


def _from_filename(filename: str) -> tuple[str, str] | None:
    stem = filename.rsplit(".", 1)[0]
    for pat in _FILENAME_PATTERNS:
        m = re.search(pat, stem)
        if m and m.lastindex >= 2:
            first, last = m.group(1), m.group(2)
            if first.upper() not in _EXCLUDE and last.upper() not in _EXCLUDE:
                return _title(first, last)
    return None


def detect_name(pdf_text: str | None, filename: str) -> tuple[str, str] | None:
    is_via = bool(re.search(r"StrengthsProfile|VIA", filename)) or (
        pdf_text and bool(re.search(r"Character\s+Strengths|VIA\s+Survey|VIA\s+Institute|Signature\s+Strengths", pdf_text))
    )
    if pdf_text:
        hit = _from_pdf_text(pdf_text, is_via)
        if hit:
            return hit
    return _from_filename(filename)


def proposed_filename(pdf_text: str | None, filename: str) -> str | None:
    """Return the .ps1-equivalent new filename, or None if we'd skip."""
    assessment = detect_assessment(pdf_text, filename)
    if not assessment:
        return None
    name = detect_name(pdf_text, filename)
    if name:
        return f"{assessment}-{name[0]}_{name[1]}.pdf"
    return f"{assessment}-Unknown-Client.pdf"


# ponytail: minimal self-check — synthetic names only, one line per shape we care about.
if __name__ == "__main__":
    # Baseline: dash + lowercase name, no PDF text.
    fn = "O_NET Interest Profiler-foo bar.pdf"
    assert detect_assessment(None, fn) == "O_NET_Interest_Profiler"
    assert detect_name(None, fn) == ("Foo", "Bar")
    assert proposed_filename(None, fn) == "O_NET_Interest_Profiler-Foo_Bar.pdf"

    # VIA: assessment from PDF text, name from "Printed for:" (hyphens + apostrophes preserved).
    text = "VIA Character Strengths\nPrinted for: Aa-Bb Cc'Dd"
    assert detect_assessment(text, "whatever.pdf") == "VIA_Character_Strengths_Profile"
    assert detect_name(text, "StrengthsProfile-Aa-Bb-CcDd.pdf") == ("Aa-Bb", "Cc'Dd")

    # Regression: leading space after dash. Prior versions returned Unknown-Client here.
    assert detect_name(None, "O_NET Interest Profiler- foo bar.pdf") == ("Foo", "Bar")

    # This app's own committed-output shape (backfill_share reads these).
    assert detect_name(None, "O_NET_Interest_Profiler-Aiyanna_Watson.pdf") == ("Aiyanna", "Watson")
    assert detect_name(None, "VIA_Character_Strengths_Profile-Shana_Beach.pdf") == ("Shana", "Beach")

    print("classify.py self-check OK")
