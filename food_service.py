"""
Food analysis and recommendation service with OpenRouter AI integration and YouTube support.
Uses automatic model fallback for reliability.
"""

import os
from openai import OpenAI
import base64
import requests

# OpenRouter AI Integrations (Replit AI - no API key needed)
AI_INTEGRATIONS_OPENROUTER_API_KEY = os.environ.get("AI_INTEGRATIONS_OPENROUTER_API_KEY", "dummy_key")
AI_INTEGRATIONS_OPENROUTER_BASE_URL = os.environ.get("AI_INTEGRATIONS_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# OpenRouter Client
openrouter = OpenAI(
    api_key=AI_INTEGRATIONS_OPENROUTER_API_KEY,
    base_url=AI_INTEGRATIONS_OPENROUTER_BASE_URL
)

# YouTube API Key (optional)
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", None)

# Free models สำหรับแนะนำเมนูอาหาร (OpenRouter เท่านั้น)
MODELS = [
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "minimax/minimax-m2.5:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-3-27b-it:free",
    "arcee-ai/trinity-large-preview:free",
    "z-ai/glm-4.5-air:free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-3-12b-it:free",
    "google/gemma-3-4b-it:free",
    "google/gemma-3n-e4b-it:free",
    "google/gemma-3n-e2b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
    "qwen/qwen3-coder:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen3-30b-a3b:free",
    "qwen/qwen3-14b:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "mistralai/mixtral-8x7b-instruct:free",
    "nvidia/llama-3.3-nemotron-super-49b-v1:free",
    "nvidia/llama-3.1-nemotron-70b-instruct:free",
    "microsoft/phi-4-mini-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "tngtech/deepseek-r1t-chimera:free",
    "liquid/lfm-40b:free",
    "huggingfaceh4/zephyr-7b-beta:free",
    "openchat/openchat-7b:free",
    "gryphe/mythomax-l2-13b:free",
    "undi95/toppy-m-7b:free",
    "01-ai/yi-34b-chat:free",
    "openrouter/auto",
    "openrouter/free",
]

def call_openrouter_with_fallback(messages: list, max_tokens: int = 2048) -> str:
    """
    Call OpenRouter with automatic model fallback for reliability.
    Tries models in order, returns first successful response.
    """
    last_error = None
    
    for model in MODELS:
        try:
            response = openrouter.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            print(f"Model {model} failed, trying next: {str(e)}")
            continue
    
    raise Exception(f"All OpenRouter models failed. Last error: {str(last_error)}")

def analyze_food_with_ai(food_name: str, user_age: int = None, user_bmi: float = None) -> str:
    """
    Analyze food nutrition using OpenRouter AI.
    """
    context = ""
    if user_age:
        context = f"ผู้ใช้อายุ {user_age} ปี"
    if user_bmi:
        context += f" BMI {user_bmi:.1f}" if context else f"BMI {user_bmi:.1f}"
    
    prompt = f"""โปรดวิเคราะห์อาหารต่อไปนี้เป็นภาษาไทยอย่างละเอียด:

ชื่อเมนู: {food_name}
{f"ข้อมูลผู้ใช้: {context}" if context else ""}

โปรดให้ข้อมูลดังนี้:
1. สารอาหารหลัก (พลังงาน, โปรตีน, ไขมัน, คาร์โบไฮเดรต)
2. วิตามินและแร่ธาตุ
3. ประโยชน์ต่อสุขภาพและชะลอวัย
4. คะแนนชะลอวัย (Longevity Score) 0-10
5. คำแนะนำการรับประทาน

ตอบให้กระชับและเป็นประโยชน์"""

    messages = [{"role": "user", "content": prompt}]
    
    try:
        result = call_openrouter_with_fallback(messages)
        return result
    except Exception as e:
        print(f"AI analysis failed: {str(e)}")
        return f"ไม่สามารถวิเคราะห์ได้ในตอนนี้ ({str(e)})"

def recommend_food_with_ai(user_age: int, user_bmi: float, dietary_preference: str = None) -> str:
    """
    Generate personalized food recommendation using OpenRouter AI.
    """
    diet_info = ""
    if user_bmi > 25:
        diet_info = "ผู้ใช้มี BMI สูง ควรเน้นเมนูต่ำแคลอรี่ สูงโปรตีน"
    elif user_bmi < 18.5:
        diet_info = "ผู้ใช้มี BMI ต่ำ ควรเน้นเมนูสูงแคลอรี่และโปรตีน"
    else:
        diet_info = "ผู้ใช้มี BMI ปกติ ควรรักษาสมดุลอาหาร"
    
    if dietary_preference:
        diet_info += f"\nความชอบพิเศษ: {dietary_preference}"
    
    prompt = f"""สร้างเมนูอาหารแนะนำสำหรับชะลอวัยเป็นภาษาไทย:

ข้อมูลผู้ใช้:
- อายุ: {user_age} ปี
- BMI: {user_bmi:.1f}
- {diet_info}

โปรดให้:
1. ชื่อเมนู (ภาษาไทยและอังกฤษ)
2. วัตถุดิบหลัก (อย่างน้อย 4-5 รายการ)
3. ประโยชน์และสารอาหาร
4. วิธีทำสั้นๆ (3-4 ขั้นตอน)
5. คะแนนชะลอวัย (0-10)

ออกแบบเมนูให้เหมาะสมกับอายุและสถานะสุขภาพของผู้ใช้"""

    messages = [{"role": "user", "content": prompt}]
    
    try:
        result = call_openrouter_with_fallback(messages)
        return result
    except Exception as e:
        print(f"AI recommendation failed: {str(e)}")
        return f"ไม่สามารถแนะนำได้ในตอนนี้ ({str(e)})"

def search_youtube_recipes(food_name: str) -> list:
    """
    Search for food recipe videos on YouTube.
    Returns empty list if YouTube API key not configured.
    """
    if not YOUTUBE_API_KEY:
        print("YouTube API key not configured")
        return []
    
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "q": f"วิธีทำ {food_name} สูตรอาหาร",
            "part": "snippet",
            "type": "video",
            "maxResults": 3,
            "key": YOUTUBE_API_KEY,
            "relevanceLanguage": "th"
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        videos = []
        for item in data.get("items", []):
            video_id = item["id"]["videoId"]
            title = item["snippet"]["title"]
            videos.append({
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "video_id": video_id
            })
        
        return videos
    except Exception as e:
        print(f"YouTube search failed: {str(e)}")
        return []
