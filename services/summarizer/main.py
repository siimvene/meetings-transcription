"""Summarization service — sends transcripts to Gemma 4 31B via vLLM for meeting summaries."""

import os
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Meeting Summarizer")

VLLM_BASE_URL = os.environ["VLLM_BASE_URL"]
VLLM_API_KEY = os.environ["VLLM_API_KEY"]
VLLM_MODEL = os.environ["VLLM_MODEL"]

SYSTEM_PROMPT = """Sa oled koosolekute kokkuvõtja. Koosta struktureeritud kokkuvõte järgmises vormingus:

## Koosoleku kokkuvõte

### Osalejad
- Nimekiri kõigist kõnelejatest

### Peamised teemad
- Arutatud teemade loetelu

### Otsused
- Kõik tehtud otsused ja kokkulepped

### Ülesanded
- [ ] Ülesanne koos vastutava isikuga (kui tuvastatav)

### Kokkuvõte
Lühike 2-3 lõiku kokkuvõte koosolekust.

Reeglid:
- Ole lühike ja faktiline
- Koosta kokkuvõte samas keeles, milles koosolek toimus. Kui koosolek oli segakeelne, kasuta enamuskeelt.
- Kui kõnelejad on tuvastatud ainult numbriga (Speaker 1, Speaker 2), säilita need märgised
- Do not invent information not present in the transcript"""


class SummarizeRequest(BaseModel):
    transcript: str
    language: str = "auto"


class SummarizeResponse(BaseModel):
    summary: str
    model: str
    prompt_tokens: int
    completion_tokens: int


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(request: SummarizeRequest):
    if not request.transcript.strip():
        raise HTTPException(status_code=400, detail="Empty transcript")

    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            f"{VLLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {VLLM_API_KEY}"},
            json={
                "model": VLLM_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": request.transcript},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            },
        )

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"vLLM error: {response.status_code} {response.text[:500]}",
        )

    data = response.json()
    return SummarizeResponse(
        summary=data["choices"][0]["message"]["content"],
        model=data["model"],
        prompt_tokens=data["usage"]["prompt_tokens"],
        completion_tokens=data["usage"]["completion_tokens"],
    )
