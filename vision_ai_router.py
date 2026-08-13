"""
Vision AI Router & Fallback Pipeline
-----------------------------------
Rank 1: NVIDIA NIM (meta/llama-3.2-11b-vision-instruct) - Primary Vision (0.67s)
Rank 2: Google Gemini (gemini-2.5-flash) - Fallback Vision & Multimodal (1.2s)
Rank 3: Groq (llama-3.3-70b-versatile) - Ultra-Fast Text Reasoning (0.3s)
"""

import os
import base64
import time
import logging
import requests
from io import BytesIO
from typing import Union, Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger("VisionAIRouter")

# API Keys Configuration (Uses Environment Variables or Runtime Parameters)
NVIDIA_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

def _encode_image_to_base64(image_input: Union[str, bytes]) -> str:
    """Helper to convert filepath or raw bytes to base64 string."""
    if isinstance(image_input, str):
        if os.path.exists(image_input):
            with open(image_input, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        elif image_input.startswith("data:image"):
            return image_input.split(",")[1]
        else:
            return image_input
    elif isinstance(image_input, bytes):
        return base64.b64encode(image_input).decode("utf-8")
    else:
        raise ValueError("Unsupported image input type. Use file path or bytes.")

def call_nvidia_vision(image_input: Union[str, bytes], prompt: str = "Describe this image in detail.", max_tokens: int = 512, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Rank 1: NVIDIA NIM (meta/llama-3.2-11b-vision-instruct)"""
    t0 = time.time()
    b64_img = _encode_image_to_base64(image_input)
    key = api_key or NVIDIA_API_KEY or os.environ.get("NVIDIA_API_KEY", "")
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "meta/llama-3.2-11b-vision-instruct",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64_img}"}
                    }
                ]
            }
        ],
        "temperature": 0.2,
        "max_tokens": max_tokens
    }
    
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    latency = round(time.time() - t0, 2)
    
    if response.status_code == 200:
        ans = response.json()["choices"][0]["message"]["content"]
        return {
            "success": True,
            "provider": "NVIDIA NIM",
            "model": "meta/llama-3.2-11b-vision-instruct",
            "latency_sec": latency,
            "text": ans.strip()
        }
    else:
        raise Exception(f"NVIDIA API Error ({response.status_code}): {response.text}")

def call_gemini_vision(image_input: Union[str, bytes], prompt: str = "Describe this image in detail.", api_key: Optional[str] = None) -> Dict[str, Any]:
    """Rank 2: Google Gemini (gemini-2.5-flash)"""
    t0 = time.time()
    b64_img = _encode_image_to_base64(image_input)
    key = api_key or GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": b64_img}}
            ]
        }]
    }
    
    response = requests.post(url, json=payload, timeout=25)
    latency = round(time.time() - t0, 2)
    
    if response.status_code == 200:
        ans = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return {
            "success": True,
            "provider": "Google Gemini",
            "model": "gemini-2.5-flash",
            "latency_sec": latency,
            "text": ans.strip()
        }
    else:
        raise Exception(f"Gemini API Error ({response.status_code}): {response.text}")

def call_groq_text(prompt: str, system_prompt: Optional[str] = None, max_tokens: int = 1024, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Rank 3: Groq (llama-3.3-70b-versatile) - Fast Text Reasoning"""
    t0 = time.time()
    key = api_key or GROQ_API_KEY or os.environ.get("GROQ_API_KEY", "")
    
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2
    }
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    latency = round(time.time() - t0, 2)
    
    if response.status_code == 200:
        ans = response.json()["choices"][0]["message"]["content"]
        return {
            "success": True,
            "provider": "Groq",
            "model": "llama-3.3-70b-versatile",
            "latency_sec": latency,
            "text": ans.strip()
        }
    else:
        raise Exception(f"Groq API Error ({response.status_code}): {response.text}")

def analyze_vision(image_input: Union[str, bytes], prompt: str = "Describe what you see in this screenshot.", fallback: bool = True) -> Dict[str, Any]:
    """
    Main Vision Function with Automatic Hierarchy & Fallback:
    1. Primary: NVIDIA NIM (meta/llama-3.2-11b-vision-instruct)
    2. Fallback: Google Gemini (gemini-2.5-flash)
    """
    # 1. Try Rank 1: NVIDIA NIM
    try:
        logger.info("Executing Rank 1 Vision: NVIDIA NIM (meta/llama-3.2-11b-vision-instruct)...")
        result = call_nvidia_vision(image_input, prompt)
        logger.info(f"✅ NVIDIA NIM Success ({result['latency_sec']}s)")
        return result
    except Exception as err:
        logger.warning(f"⚠️ Rank 1 (NVIDIA NIM) failed: {err}")
        if not fallback:
            return {"success": False, "error": str(err)}
            
    # 2. Try Rank 2 Fallback: Gemini 2.5 Flash
    try:
        logger.info("Executing Rank 2 Fallback Vision: Google Gemini (gemini-2.5-flash)...")
        result = call_gemini_vision(image_input, prompt)
        logger.info(f"✅ Google Gemini Success ({result['latency_sec']}s)")
        return result
    except Exception as err:
        logger.error(f"❌ Rank 2 (Google Gemini) failed: {err}")
        return {"success": False, "error": f"All vision providers failed. Last error: {err}"}

def vision_pipeline(image_input: Union[str, bytes], vision_prompt: str, reasoning_prompt: Optional[str] = None) -> Dict[str, Any]:
    """
    Full End-to-End Pipeline:
    Step 1: Visual Extraction using Rank 1 (NVIDIA NIM) / Rank 2 (Gemini)
    Step 2: Instant Reasoning using Rank 3 (Groq llama-3.3-70b-versatile)
    """
    # Step 1: Vision analysis
    v_res = analyze_vision(image_input, vision_prompt)
    if not v_res.get("success"):
        return v_res
        
    visual_text = v_res["text"]
    
    # Step 2: Reasoning (if reasoning_prompt provided)
    if reasoning_prompt:
        combined_prompt = f"Visual Context:\n{visual_text}\n\nTask Instructions:\n{reasoning_prompt}"
        try:
            logger.info("Executing Rank 3 Reasoning: Groq (llama-3.3-70b-versatile)...")
            r_res = call_groq_text(combined_prompt)
            logger.info(f"✅ Groq Reasoning Success ({r_res['latency_sec']}s)")
            return {
                "success": True,
                "vision_provider": v_res["provider"],
                "vision_latency": v_res["latency_sec"],
                "reasoning_provider": r_res["provider"],
                "reasoning_latency": r_res["latency_sec"],
                "total_latency_sec": round(v_res["latency_sec"] + r_res["latency_sec"], 2),
                "visual_description": visual_text,
                "final_answer": r_res["text"]
            }
        except Exception as err:
            logger.warning(f"⚠️ Groq Reasoning failed, returning vision analysis: {err}")
            
    return v_res

if __name__ == "__main__":
    print("=== VISION AI ROUTER MODULE LOADED ===")
