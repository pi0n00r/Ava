"""
Ollama API endpoints for Admin UI.

Provides endpoints to:
- Test connection to Ollama instance
- List available models
- Check model tool calling capabilities
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import aiohttp
import asyncio

router = APIRouter(prefix="/api/ollama", tags=["ollama"])

# Models known to support tool calling
TOOL_CAPABLE_MODELS = {
    "llama3.2", "llama3.1", "llama3", "llama3.2:1b", "llama3.2:3b",
    "mistral", "mistral-nemo", "mistral:7b",
    "qwen2.5", "qwen2.5:7b", "qwen2.5:14b", "qwen2",
    "command-r", "command-r-plus",
    "nemotron", "granite3-dense",
}


class OllamaTestRequest(BaseModel):
    base_url: str = "http://localhost:11434"


class OllamaModel(BaseModel):
    name: str
    size: int = 0
    modified_at: str = ""
    tools_capable: bool = False


class OllamaTestResponse(BaseModel):
    success: bool
    message: str
    models: List[OllamaModel] = []
    configured_model: Optional[str] = None
    hint: Optional[str] = None


class OllamaModelsResponse(BaseModel):
    success: bool
    models: List[OllamaModel] = []
    error: Optional[str] = None


def _model_supports_tools(model_name: str) -> bool:
    """Check if model is known to support tool calling."""
    model_base = model_name.split(":")[0].lower()
    return model_base in TOOL_CAPABLE_MODELS


@router.post("/test", response_model=OllamaTestResponse)
async def test_ollama_connection(request: OllamaTestRequest):
    """
    Test connection to an Ollama instance.
    
    Returns list of available models and their tool calling capabilities.
    """
    base_url = request.base_url.rstrip("/")
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}/api/tags"
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    raw_models = data.get("models", [])
                    
                    models = [
                        OllamaModel(
                            name=m.get("name", ""),
                            size=m.get("size", 0),
                            modified_at=m.get("modified_at", ""),
                            tools_capable=_model_supports_tools(m.get("name", "")),
                        )
                        for m in raw_models
                    ]
                    
                    return OllamaTestResponse(
                        success=True,
                        message=f"Connected! Found {len(models)} models.",
                        models=models,
                    )
                else:
                    body = await response.text()
                    return OllamaTestResponse(
                        success=False,
                        message=f"Ollama returned status {response.status}",
                        hint=body[:200] if body else None,
                    )
                    
    except asyncio.TimeoutError:
        return OllamaTestResponse(
            success=False,
            message="Connection timeout - is Ollama running?",
            hint="Run: OLLAMA_HOST=0.0.0.0 ollama serve",
        )
    except aiohttp.ClientConnectorError as e:
        return OllamaTestResponse(
            success=False,
            message=f"Cannot connect to Ollama",
            hint="Ensure Ollama is running. For Docker, use your host machine's IP address, not localhost.",
        )
    except Exception as e:
        return OllamaTestResponse(
            success=False,
            message=f"Connection failed: {str(e)}",
        )


@router.get("/models", response_model=OllamaModelsResponse)
async def list_ollama_models(base_url: str = "http://localhost:11434"):
    """
    List available models from an Ollama instance.
    
    Query param: base_url - The Ollama server URL (default: http://localhost:11434)
    """
    base_url = base_url.rstrip("/")
    
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{base_url}/api/tags"
            timeout = aiohttp.ClientTimeout(total=10)
            
            async with session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    raw_models = data.get("models", [])
                    
                    models = [
                        OllamaModel(
                            name=m.get("name", ""),
                            size=m.get("size", 0),
                            modified_at=m.get("modified_at", ""),
                            tools_capable=_model_supports_tools(m.get("name", "")),
                        )
                        for m in raw_models
                    ]
                    
                    return OllamaModelsResponse(
                        success=True,
                        models=models,
                    )
                else:
                    return OllamaModelsResponse(
                        success=False,
                        error=f"Ollama returned status {response.status}",
                    )
                    
    except Exception as e:
        return OllamaModelsResponse(
            success=False,
            error=str(e),
        )


@router.get("/tool-capable-models")
async def get_tool_capable_models():
    """
    Get list of models known to support tool calling.
    
    This is a static list based on Ollama documentation.
    """
    return {
        "models": sorted(TOOL_CAPABLE_MODELS),
        "note": "These models support function/tool calling. Other models will work but without tool support.",
    }
