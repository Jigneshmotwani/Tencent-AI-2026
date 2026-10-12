"""Bounded text-PDF extraction in a killable process, with no OCR or disk content cache."""
import asyncio
import io
import sys
from pathlib import Path

from ..config import settings
from ..contracts import SourceError


def extract(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError('unsupported')
    output = []
    remaining = 200_000
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ''
        if len(text) > remaining:
            raise ValueError('unsupported')
        remaining -= len(text)
        if text.strip():
            output.append(f'[Page {number}]\n{text}')
    return '\n\n'.join(output)


async def extract_pdf(data: bytes) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'app.connectors.pdf_extract',
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(data),
                                          timeout=min(8.0, settings().source_timeout))
        if process.returncode != 0 or len(output) > settings().max_content_bytes:
            raise SourceError('unsupported')
        return output.decode('utf-8')
    except TimeoutError:
        raise SourceError('unsupported') from None
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


if __name__ == '__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
        # macOS does not consistently enforce RLIMIT_AS; CPU/process lifetime still bounded.
        if sys.platform != 'darwin':
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        raw = sys.stdin.buffer.read(settings().max_content_bytes + 1)
        if len(raw) > settings().max_content_bytes:
            raise ValueError('unsupported')
        sys.stdout.write(extract(raw))
    except Exception:
        sys.exit(2)
