"""Force-close Adobe PDF viewers on a remote PC via taskkill /S.

Uses Windows built-in taskkill.exe over RPC — same creds as SMB, no WinRM required.
"""
import subprocess

# Adobe process names in the wild. AcroRd32/64 are legacy; Acrobat.exe is current.
_PDF_READER_IMAGES = ("Acrobat.exe", "AcroRd32.exe", "AcroRd64.exe")


def kill_pdf_readers(host: str, user: str, password: str, timeout: int = 30) -> list[str]:
    """Try to kill each known Adobe process. Returns list of images that were terminated."""
    killed = []
    for image in _PDF_READER_IMAGES:
        r = subprocess.run(
            ["taskkill", "/S", host, "/U", user, "/P", password, "/IM", image, "/F"],
            capture_output=True, text=True, timeout=timeout,
        )
        # taskkill exits 0 on success, 128 when process not found. Only 0 means we killed something.
        if r.returncode == 0:
            killed.append(image)
    return killed
