from backends.registry import LLM_REGISTRY
from backends.llm.llama_cpp_backend import LlamaCppBackend

LLM_REGISTRY.register(LlamaCppBackend)
