"""Original news-feed / content pipeline from app.py, preserved.

Function bodies are unchanged except:
  * API keys come from the environment instead of being hardcoded;
  * heavy imports (torch/diffusers/ollama/moviepy) are lazy so the web app
    starts without a GPU;
  * init_pipe()/init_animatediff() delegate to the shared GPU model manager.
"""
import json
import os
import re
import wave
from datetime import datetime

import feedparser
import numpy as np
import requests
from PIL import Image

from ..config import Config
from . import models
from .llm import ollama_chat as _ollama_chat


class _OllamaShim:
    """Keeps the original `ollama.chat(model=..., messages=...)` call sites."""
    @staticmethod
    def chat(model, messages):
        return _ollama_chat(messages, model=None if model == "llama3" else model)


ollama = _OllamaShim()

PEXELS_KEY = Config.PEXELS_KEY
PIXABAY_KEY = Config.PIXABAY_KEY
generation_lock = models.generation_lock


def _moviepy():
    import moviepy
    return moviepy


def ImageClip(*a, **k): return _moviepy().ImageClip(*a, **k)
def AudioFileClip(*a, **k): return _moviepy().AudioFileClip(*a, **k)
def TextClip(*a, **k): return _moviepy().TextClip(*a, **k)
def VideoFileClip(*a, **k): return _moviepy().VideoFileClip(*a, **k)
def CompositeVideoClip(*a, **k): return _moviepy().CompositeVideoClip(*a, **k)
def CompositeAudioClip(*a, **k): return _moviepy().CompositeAudioClip(*a, **k)
def concatenate_videoclips(*a, **k): return _moviepy().concatenate_videoclips(*a, **k)


def gTTS(*a, **k):
    from gtts import gTTS as _g
    return _g(*a, **k)


def _legacy_font():
    from .render import resolve_font
    return resolve_font()


def init_pipe():
    return models.get_sd()


def init_animatediff():
    return models.get_animatediff()

# ------------------------------
# GLOBAL CONFIG
# ------------------------------

NEWS_COUNT = 1
SCENE_COUNT = 10
SCENE_DURATION = 4.5

# ------------------------------
# 1 Generate script
# ------------------------------

    
def generate_script(news, format_type="short"):

    # 🔥 LONG VIDEO (YouTube Normal)
    if format_type == "youtube":

        prompt = f"""
Create a YouTube video narration.

STRICT RULES:
- ONLY narration text
- NO introduction like "Here is..."
- NO disclaimers
- NO warnings
- NO extra text
- NO quotes
- NO headings
- start directly with narration
- 25 to 35 sentences
- storytelling style
- engaging and informative
- suitable for 2–4 minute video

News:
{news}
"""

    # 🔥 SHORT FORMAT (Shorts / Instagram)
    else:

        prompt = f"""
Create a YouTube Shorts narration.

STRICT RULES:
- ONLY narration text
- NO introduction like "Here is..."
- NO disclaimers
- NO warnings
- NO extra text
- NO quotes
- NO headings
- start directly with narration
- 10 to 12 short sentences
- fast-paced and engaging

News:
{news}
"""

    r = ollama.chat(
        model="llama3",
        messages=[{"role": "user", "content": prompt}]
    )

    return r["message"]["content"]


    


def sanitize_keyword(keyword):

    banned_words = [
        "covid", "pandemic", "virus", "coronavirus",
        "outbreak", "health emergency", "medical emergency"
    ]

    replacements = {
        "emergency": "government crisis meeting",
        "crisis": "international leaders meeting",
        "alert": "security response scene"
    }

    keyword = keyword.lower()

    for bad, repl in replacements.items():
        if bad in keyword:
            keyword = keyword.replace(bad, repl)

    for bad in banned_words:
        keyword = keyword.replace(bad, "")

    return keyword.strip()


def detect_scenes(script):

    prompt = f"""
Break the following script into {SCENE_COUNT} scenes.

Return ONLY valid JSON.

Format:
[
{{"text":"short narration","keyword":"specific stock image search query"}}
]

STRICT RULES:
- exactly {SCENE_COUNT} scenes
- keyword must be 4–6 words
- keyword must describe a visible scene
- include location or object if possible
- suitable for stock photo search
- avoid vague words like "breaking news", "event", "update"
- avoid words related to health, virus, pandemic

GOOD examples:
"Iran missile launch night sky"
"Abu Dhabi international airport runway"
"military air defense system firing"
"United Nations security council meeting"
"fighter jet taking off runway"
"airport terminal security evacuation"

BAD examples:
"breaking news"
"politics discussion"
"future possibility"

Script:
{script}
"""

    r = ollama.chat(
        model="llama3",
        messages=[{"role": "user", "content": prompt}]
    )

    content = r["message"]["content"]

    print("\nRAW LLM OUTPUT:\n", content)

    start = content.find("[")
    end = content.rfind("]")

    if start != -1 and end != -1:
        try:
            scenes = json.loads(content[start:end+1])

            cleaned = []

            for s in scenes:

                text = s.get("text", "").strip()

                keyword = s.get("keyword", "").replace('"', "").strip()

                keyword = sanitize_keyword(keyword)[:60]

                cleaned.append({
                    "text": text,
                    "keyword": keyword
                })

            cleaned = cleaned[:SCENE_COUNT]

            return cleaned

        except Exception as e:
            print("JSON parse error:", e)

    print("⚠️ Using fallback scenes")

    sentences = [s.strip() for s in script.split(".") if s.strip()]

    scenes = []

    for s in sentences[:SCENE_COUNT]:

        keyword = sanitize_keyword(s[:60])

        scenes.append({
            "text": s,
            "keyword": keyword
        })

    while len(scenes) < SCENE_COUNT:

        scenes.append({
            "text": sentences[-1],
            "keyword": sanitize_keyword(sentences[-1][:60])
        })

    return scenes






# ------------------------------
# 3 AI images
# ------------------------------
def is_black_image(img):
    arr = np.array(img)
    return arr.mean() < 5


def generate_sound(keyword, duration, i):

    sr = 44100  # sample rate
    t = np.linspace(0, duration, int(sr * duration))

    k = keyword.lower()

    # 🐱 CAT (random pitch meow)
    if "cat" in k:
        freq = np.random.uniform(400, 800)
        audio = 0.5 * np.sin(2 * np.pi * freq * t)

    # 🌧️ RAIN (white noise)
    elif "rain" in k:
        audio = np.random.normal(0, 0.3, len(t))

    # 🌊 OCEAN (low wave noise)
    elif "ocean" in k or "sea" in k:
        audio = np.sin(2 * np.pi * 60 * t) + np.random.normal(0, 0.2, len(t))

    # 🌌 SPACE / MOON (deep ambient hum)
    elif "moon" in k or "space" in k:
        audio = np.sin(2 * np.pi * 40 * t)

    # 🔥 FIRE (crackling noise)
    elif "fire" in k:
        audio = np.random.normal(0, 0.4, len(t))

    # 🚗 CITY (mixed noise)
    elif "city" in k:
        audio = np.sin(2 * np.pi * 120 * t) + np.random.normal(0, 0.3, len(t))

    # 🌳 FOREST (soft noise)
    elif "forest" in k:
        audio = np.random.normal(0, 0.2, len(t))

    else:
        audio = np.random.normal(0, 0.1, len(t))

    # normalize
    audio = audio / np.max(np.abs(audio))

    path = f"sound_{i}.wav"

    with wave.open(path, "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((audio * 32767).astype(np.int16))

    return path



def create_video_from_clips(video_paths, scenes, output, format_type="short"):

    if format_type == "short":
        width, height = 512, 768
    elif format_type == "instagram":
        width, height = 512, 512
    else:
        width, height = 768, 512

    clips = []
    audio_clips = []

    for i, v in enumerate(video_paths):

        clip = VideoFileClip(v)

        # 🔥 resize based on format
        clip = clip.resized(height=height).with_position("center")

        bg = CompositeVideoClip([clip], size=(width, height)).with_duration(clip.duration)

        clips.append(bg)

        sound_path = generate_sound(
            scenes[i]["keyword"],
            clip.duration,
            i
        )

        audio = AudioFileClip(sound_path)
        audio_clips.append(audio)

    final = concatenate_videoclips(clips, method="compose")

    final_audio = CompositeAudioClip(audio_clips)

    final = final.with_audio(final_audio)

    final.write_videofile(
        output,
        fps=30,
        codec="libx264",
        audio_codec="aac"
    )
    
def generate_ai_image(prompt, i, format_type, news_context=""):

    import torch  # noqa

    pipe = init_pipe()   # ✅ reuse model

    if format_type == "short":
        width, height = 512, 768
    elif format_type == "instagram":
        width, height = 512, 512
    else:
        width, height = 768, 512

    safe_prompt = f"""
    professional news photography, photojournalism

    REAL WORLD SCENE:
    {prompt}

    NEWS CONTEXT:
    {news_context}

    VISUAL REQUIREMENTS:
    - show actual event clearly
    - include people, location, action
    - realistic environment matching the news
    - no abstract or symbolic images

    STYLE:
    DSLR photo, natural lighting, sharp focus, realistic colors
    """

    negative_prompt = """
    text, watermark, logo, gettyimages, signature,
    blurry, distorted, low quality,
    ugly, bad anatomy, deformed face
    """

    generator = torch.Generator(device="cuda").manual_seed(42 + i)

    try:
        with generation_lock:   # 🔥 CRITICAL
            with torch.inference_mode():

                image = pipe(
                    prompt=safe_prompt,
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    num_inference_steps=18,
                    guidance_scale=5.5,
                    generator=generator
                ).images[0]

    except Exception as e:
        print("❌ SDXL failed:", e)

        # fallback (but now rare)
        from PIL import Image
        image = Image.new("RGB", (width, height), color=(30, 30, 30))

    path = f"scene_ai_{i}.jpg"
    image.save(path)

    # light cleanup only
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return path

# ------------------------------
# 3 Download images
# ------------------------------
# ------------------------------
# 3 Download images
# ------------------------------



def is_valid_image(path):

    try:
        img = Image.open(path)

        if img.size[0] < 200 or img.size[1] < 200:
            return False

        img = img.convert("RGB")
        img.save(path)

        return True

    except:
        return False

def resize_for_format(path, index, format_type):

    img = Image.open(path)

    if format_type == "short":
        target = (1080,1920)

    elif format_type == "instagram":
        target = (1080,1080)

    else:
        target = (1920,1080)

    img = img.resize(target)

    new_path = f"scene_{index}.jpg"

    img.save(new_path)

    return new_path

def make_vertical(path, index):

    img = Image.open(path)

    w, h = img.size

    target_ratio = 9 / 16
    current_ratio = w / h

    if current_ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))

    img = img.resize((1080, 1920))

    new_path = f"scene_vertical_{index}.jpg"

    img.save(new_path)

    return new_path


def download_images(scenes, format_type, ai_only=False):

    headers = {"Authorization": PEXELS_KEY}

    paths = []
    sources = []

    for i, scene in enumerate(scenes):

        query = scene.get("keyword", "world news event")
        text = scene.get("text", "")

        img_url = None
        source = None

        print("Searching:", query)

        # =====================================================
        # ✅ AI ONLY MODE (SKIP PEXELS + PIXABAY COMPLETELY)
        # =====================================================
        if ai_only:
            print("🎨 AI ONLY MODE ENABLED")

            ai_prompt = f"""
            breaking news photography

            news event: {text}

            ultra realistic
            cinematic lighting
            DSLR photo
            Reuters style
            sharp focus
            8k quality
            highly detailed
            """

            path = generate_ai_image(ai_prompt, i, format_type)

            paths.append(path)
            sources.append("AI")

            continue

        # =====================================================
        # ----------- PEXELS ------------
        # =====================================================
        try:
            url = f"https://api.pexels.com/v1/search?query={query}&per_page=8"

            r = requests.get(url, headers=headers, timeout=10).json()

            for p in r.get("photos", []):

                w = p["width"]
                h = p["height"]

                if aspect_match(w, h, format_type):
                    img_url = p["src"]["large"]
                    source = "Pexels"
                    break

        except Exception as e:
            print("Pexels error:", e)

        # =====================================================
        # ----------- PIXABAY ------------
        # =====================================================
        if not img_url:
            try:
                url = f"https://pixabay.com/api/?key={PIXABAY_KEY}&q={query}&image_type=photo&per_page=8"

                r = requests.get(url, timeout=10).json()

                for hit in r.get("hits", []):

                    w = hit["imageWidth"]
                    h = hit["imageHeight"]

                    if aspect_match(w, h, format_type):
                        img_url = hit["largeImageURL"]
                        source = "Pixabay"
                        break

            except Exception as e:
                print("Pixabay error:", e)

        # =====================================================
        # ----------- DOWNLOAD IMAGE ------------
        # =====================================================
        if img_url:
            try:
                data = requests.get(img_url, timeout=10).content

                path = f"scene{i}.jpg"

                with open(path, "wb") as f:
                    f.write(data)

                img = Image.open(path).convert("RGB")
                img.save(path)

                paths.append(path)
                sources.append(source)

                continue

            except Exception as e:
                print("Image download error:", e)

        # =====================================================
        # ----------- AI FALLBACK ------------
        # =====================================================
        print("⚠️ Falling back to AI image")

        ai_prompt = f"""
        ultra realistic photojournalism image

        news event: {text}

        sharp focus
        8k photography
        highly detailed face
        realistic skin texture
        professional news camera
        Reuters style photography
        cinematic lighting
        DSLR photo
        """

        path = generate_ai_image(ai_prompt, i, format_type)

        paths.append(path)
        sources.append("AI")

    return paths, sources

# ------------------------------
# 4 Generate voice
# ------------------------------

def generate_voice(script):

    tts = gTTS(script)

    tts.save("voice.mp3")

    return "voice.mp3"


# ------------------------------
# 5 Ken Burns animation
# ------------------------------

def kenburns(img, duration):

    clip = ImageClip(img).with_duration(duration)

    # Fit image width for Shorts
    clip = clip.resized(width=1080)

    # Center the image
    clip = clip.with_position("center")

    # Gentle zoom
    clip = clip.resized(lambda t: 1 + 0.01 * t)

    # Create vertical canvas
    bg = CompositeVideoClip([clip], size=(1080,1920)).with_duration(duration)

    return bg

# ------------------------------
# 6 get news
# ------------------------------
def generate_description(news):

    prompt = f"""
Create a YouTube Shorts description.

Rules:
- 2 engaging sentences
- encourage comments
- add 6 trending hashtags

News:
{news}
"""

    r = ollama.chat(
        model="llama3",
        messages=[{"role":"user","content":prompt}]
    )

    return r["message"]["content"]



def clean_filename(title):

    # remove symbols
    title = re.sub(r'[^a-zA-Z0-9 ]', '', title)

    # limit length
    title = title[:40]

    # replace spaces
    title = title.replace(" ", "_")

    return title

def generate_title(news):

    prompt = f"""
Create a viral YouTube Shorts title.

Rules:
- Maximum 8 words
- Very engaging
- Use strong words like BREAKING, SHOCKING
- Return ONLY the title
- No explanation
- No punctuation
- No quotes

News:
{news}
"""

    r = ollama.chat(
        model="llama3",
        messages=[{"role":"user","content":prompt}]
    )

    return r["message"]["content"].strip()



def get_trending_news(count=5):

    url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"

    feed = feedparser.parse(url)

    news_list = []

    for entry in feed.entries:
        news_list.append(entry.title)

    return news_list[:count]


def filter_engaging_news(news_list):

    prompt = f"""
Select the {NEWS_COUNT} most engaging and viral news topics.

Return ONLY a JSON array of titles.

Example:
["title1","title2"]

News:
{news_list}
"""

    r = ollama.chat(
        model="llama3",
        messages=[{"role":"user","content":prompt}]
    )

    content = r["message"]["content"]

    try:
        return json.loads(content)
    except:
        return news_list[:NEWS_COUNT]

# ------------------------------
# 6 Create video
# ------------------------------

def create_video(images, audio, scenes, filename, format_type="short"):

    audio_clip = AudioFileClip(audio)
    total_duration = audio_clip.duration   # ✅ always use real voice duration

    if format_type == "short":
        width, height = 1080, 1920

    elif format_type == "instagram":
        width, height = 1080, 1080

    else:
        width, height = 1920, 1080

    scene_duration = total_duration / max(len(images), 1)
    
    clips = []

    for i, img in enumerate(images):

        text = scenes[i]["text"] if i < len(scenes) else ""

        img_pil = Image.open(img).convert("RGB")
        img_pil.save(img)

        clip = ImageClip(img).with_duration(scene_duration)

        clip = clip.resized(height=height)

        clip = clip.with_position(("center", "center"))

        clip = clip.resized(lambda t: 1 + 0.02 * t)

        bg = CompositeVideoClip(
            [clip],
            size=(width, height),
            bg_color=(0,0,0)
        ).with_duration(scene_duration)

        txt = TextClip(
            text=" " + text,
            font=_legacy_font(),
            font_size=52,
            color="white",
            stroke_color="black",
            stroke_width=3,
            size=(int(width * 0.8), None),
            method="caption"
        ).with_position(("center", height - int(height * 0.25)))\
         .with_duration(scene_duration)

        txt = txt.with_background_color(
            size=(txt.w + 60, txt.h + 40),
            color=(0,0,0),
            opacity=0.6
        )

        final = CompositeVideoClip(
            [bg, txt],
            size=(width, height)
        ).with_duration(scene_duration)

        clips.append(final)

    video = concatenate_videoclips(clips, method="compose")

    audio_clip = AudioFileClip(audio)

    if audio_clip.duration > video.duration:
        audio_clip = audio_clip.subclipped(0, video.duration)

    video = video.with_audio(audio_clip)

    video.write_videofile(
        filename,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        threads=os.cpu_count(),
        preset="ultrafast"
    )

    
# ------------------------------
# 7 Full pipeline
# ------------------------------

def get_today_folder():

    today = datetime.now().strftime("%Y-%m-%d")

    folder = os.path.join(Config.LEGACY_VIDEO_DIR, today)

    os.makedirs(folder, exist_ok=True)

    return folder




def generate_news_short(news, format_type="short", ai_only=False):

    folder = get_today_folder()

    title = generate_title(news)
    script = generate_script(news,format_type)
    scenes = detect_scenes(script)

    images, sources = download_images(scenes, format_type, ai_only)  # ✅ pass

    audio = generate_voice(script)
    description = generate_description(news)

    safe_title = clean_filename(title)
    filename = f"{folder}/{safe_title}.mp4"

    create_video(images, audio, scenes, filename, format_type)

    return {
        "news": news,
        "title": title,
        "description": description,
        "video": filename,
        "folder": folder,
        "images": " → ".join(sources)
    }

def aspect_match(width, height, format_type):

    ratio = width / height

    if format_type == "short":
        target = 9/16

    elif format_type == "instagram":
        target = 1

    else:
        target = 16/9

    tolerance = 0.25

    return abs(ratio - target) <= tolerance



def generate_video_with_reference(prompt, image_path, i):

    pipe = init_animatediff()

    from PIL import Image
    image = Image.open(image_path).convert("RGB")

    try:
        frames = pipe(
            prompt=prompt,
            image=image,
            num_frames=16,
            num_inference_steps=20,
            guidance_scale=7.5
        ).frames[0]

        import imageio
        path = f"story_scene_{i}.mp4"
        imageio.mimsave(path, frames, fps=8)

        return path

    except Exception as e:
        print("❌ AnimateDiff failed:", e)

        from moviepy import ColorClip
        fallback = f"fallback_story_{i}.mp4"
        clip = ColorClip(size=(1080,1920), color=(0,0,0), duration=3)
        clip.write_videofile(fallback, fps=24)

        return fallback

def generate_ai_video(prompt, i):

    pipe = init_animatediff()

    try:
        frames = pipe(
            prompt=prompt,
            num_frames=16,
            num_inference_steps=20,
            guidance_scale=7.5
        ).frames[0]

        import imageio
        path = f"content_scene_{i}.mp4"
        imageio.mimsave(path, frames, fps=8)

        return path

    except Exception as e:
        print("❌ AnimateDiff failed:", e)

        from moviepy import ColorClip
        fallback = f"fallback_{i}.mp4"
        clip = ColorClip(size=(1080,1920), color=(0,0,0), duration=3)
        clip.write_videofile(fallback, fps=24)

        return fallback

