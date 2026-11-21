import ollama
import requests
import json
from gtts import gTTS
from moviepy import ImageClip, AudioFileClip, TextClip
from moviepy import concatenate_videoclips, CompositeVideoClip
import os
import feedparser
import re
from datetime import datetime
import torch
from PIL import Image
from flask import Flask, request, jsonify, send_file, render_template_string
import subprocess
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import numpy as np
import threading
from diffusers import StableDiffusionXLPipeline, StableDiffusionPipeline
from fastapi import FastAPI, UploadFile, File, Form
from typing import List
from moviepy import VideoFileClip, concatenate_videoclips, AudioFileClip

from diffusers import AnimateDiffPipeline, MotionAdapter
import torch
import numpy as np
import wave

from moviepy import AudioFileClip, CompositeAudioClip


video_pipe = None


app = Flask(__name__)


pipe = None
generation_lock = threading.Lock()


PEXELS_KEY = "rqjuMsCVrasLWHz30acwiB6ThXTzyCzBZZWz0tNBwbsanFIRztyqIv6k"

PIXABAY_KEY = "54993405-a5737b60644e406dc77fc30e4"

import threading

progress_status = {
    "progress": 0,
    "status": "idle",
    "videos": [],
    "news": []
}
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


    
# ------------------------------
# 2 Scene detection
# ------------------------------
def init_pipe():
    global pipe

    if pipe is None:
        import torch
        from diffusers import StableDiffusionXLPipeline

        torch.cuda.empty_cache()

        pipe = StableDiffusionPipeline.from_pretrained(
            "SG161222/Realistic_Vision_V5.1_noVAE",
            torch_dtype=torch.float16
        ).to("cuda")

        pipe = pipe.to("cuda")

        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()

        # 🔥 KEY FIX (NOT upcast_vae)
        pipe.vae.config.force_upcast = True

    return pipe

def init_animatediff():

    global video_pipe

    if video_pipe is None:

        adapter = MotionAdapter.from_pretrained(
            "guoyww/animatediff-motion-adapter-v1-5-2"
        )

        video_pipe = AnimateDiffPipeline.from_pretrained(
            "SG161222/Realistic_Vision_V5.1_noVAE",
            motion_adapter=adapter,
            torch_dtype=torch.float16
        ).to("cuda")

        video_pipe.enable_vae_slicing()

        # ❌ REMOVE THIS
        # video_pipe.enable_model_cpu_offload()

    return video_pipe


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

    import torch

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
            font="ttfFile/DejaVuSans-Bold.ttf",
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

    folder = f"videos/{today}"

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


def generate_content_background(content, format_type):

    progress_status["progress"] = 0
    progress_status["status"] = "Starting content generation..."
    progress_status["videos"] = []

    try:
        # 1. Script
        script = generate_script(content, format_type)

        progress_status["status"] = "Analyzing scenes..."

        # 2. Scenes
        scenes = detect_scenes(script)

        video_paths = []

        total = len(scenes)

        # 3. Generate AI videos
        for i, scene in enumerate(scenes):

            progress_status["status"] = f"Generating scene {i+1}/{total}"

            prompt = f"""
            cinematic movie scene,
            real people,
            natural motion,
            camera movement,
            ultra realistic,
            {scene['text']}
            """

            v = generate_ai_video(prompt, i)
            video_paths.append(v)

            progress_status["progress"] = int(((i+1)/total)*70)

        # 4. Voice
        progress_status["status"] = "Generating voice..."
        audio = generate_voice(script)

        # 5. Merge
        progress_status["status"] = "Final rendering..."
        output = "content_video.mp4"

        create_video_from_clips(video_paths, scenes, output, format_type)

        progress_status["progress"] = 100
        progress_status["status"] = "completed"

        progress_status["videos"] = [{
            "news": "Custom Content",
            "title": "AI Generated Story",
            "description": script[:100],
            "video": output,
            "folder": "",
            "images": "AI Video"
        }]

    except Exception as e:
        progress_status["status"] = f"Error: {str(e)}"
        

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


# ------------------------------
# Todays folder
# ------------------------------
@app.route("/generate-content", methods=["POST"])
def generate_content():

    data = request.json

    content = data.get("content")
    format_type = data.get("format", "short")

    thread = threading.Thread(
        target=generate_content_background,
        args=(content, format_type)
    )

    thread.start()

    return {"status": "started"}


@app.post("/generate-story")
async def generate_story(images: List[UploadFile] = File(...),
                         format: str = Form(...)):

    try:
        # -----------------------------
        # INIT PROGRESS
        # -----------------------------
        progress_status["progress"] = 0
        progress_status["status"] = "Starting story generation..."
        progress_status["videos"] = []

        image_paths = []

        # -----------------------------
        # 1. SAVE IMAGES
        # -----------------------------
        for i, file in enumerate(images):
            path = f"story_input_{i}.jpg"
            with open(path, "wb") as f:
                f.write(await file.read())
            image_paths.append(path)

        main_image = image_paths[0]  # main reference image

        # -----------------------------
        # 2. GENERATE STORY SCRIPT
        # -----------------------------
        progress_status["status"] = "Creating story..."

        script = generate_script("cinematic story based on image", "short")

        # -----------------------------
        # 3. DETECT SCENES
        # -----------------------------
        progress_status["status"] = "Analyzing scenes..."

        scenes = detect_scenes(script)

        video_paths = []
        total = len(scenes)

        pipe = init_animatediff()

        # -----------------------------
        # 4. GENERATE VIDEO SCENES
        # -----------------------------
        from PIL import Image
        import imageio

        ref_image = Image.open(main_image).convert("RGB")

        for i, scene in enumerate(scenes):

            progress_status["status"] = f"Generating scene {i+1}/{total}"
            progress_status["progress"] = int(((i+1)/total) * 80)

            prompt = f"""
            cinematic film scene,
            same character as reference image,
            consistent face,
            realistic human motion,
            camera movement,
            natural lighting,
            high detail,
            {scene['text']}
            """

            try:
                frames = pipe(
                    prompt=prompt,
                    image=ref_image,
                    num_frames=16,
                    num_inference_steps=20,
                    guidance_scale=7.5
                ).frames[0]

                path = f"story_scene_{i}.mp4"
                imageio.mimsave(path, frames, fps=8)

            except Exception as e:
                print("❌ AnimateDiff failed:", e)

                # fallback video
                from moviepy import ColorClip
                fallback = f"fallback_{i}.mp4"
                clip = ColorClip(size=(1080,1920), color=(0,0,0), duration=3)
                clip.write_videofile(fallback, fps=24)
                path = fallback

            video_paths.append(path)

        # -----------------------------
        # 5. GENERATE VOICE
        # -----------------------------
        progress_status["status"] = "Generating voice..."
        audio = generate_voice(script)

        # -----------------------------
        # 6. FINAL MERGE
        # -----------------------------
        progress_status["status"] = "Rendering final video..."

        output = "story_cinematic.mp4"

        create_video_from_clips(video_paths, scenes, output, format_type)

        # -----------------------------
        # DONE
        # -----------------------------
        progress_status["progress"] = 100
        progress_status["status"] = "completed"

        progress_status["videos"] = [{
            "news": "Story",
            "title": "Cinematic Story",
            "description": script[:120],
            "video": output,
            "folder": "",
            "images": "AI Video"
        }]

        return {"status": "completed"}

    except Exception as e:
        progress_status["status"] = f"Error: {str(e)}"
        return {"status": "error", "message": str(e)}


@app.route("/upload_youtube", methods=["POST"])
def upload_youtube():

    try:

        print("\n==============================")
        print("🚀 YOUTUBE UPLOAD STARTED")
        print("==============================")

        data = request.json

        video_path = data["video"]
        title = data["title"]
        description = data["description"]

        print("📁 Video Path:", video_path)
        print("📝 Title:", title)
        print("📄 Description:", description)

        scopes = ["https://www.googleapis.com/auth/youtube.upload"]

        print("🔑 Loading OAuth token...")

        creds = Credentials.from_authorized_user_file(
            "youtube_token.json", scopes
        )

        print("✅ OAuth token loaded")

        print("🌐 Connecting to YouTube API...")

        youtube = build("youtube", "v3", credentials=creds)

        print("✅ Connected to YouTube API")

        body = {
            "snippet": {
                "title": title + " #shorts",
                "description": description + "\n\n#shorts",
                "tags": ["news", "ai", "shorts"],
                "categoryId": "25"
            },
            "status": {
                "privacyStatus": "public"
            }
        }

        print("📦 Preparing video upload...")

        media = MediaFileUpload(
            video_path,
            chunksize=-1,
            resumable=True
        )

        request_upload = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        print("⬆ Upload started...")

        response = None

        while response is None:

            status, response = request_upload.next_chunk()

            if status:
                percent = int(status.progress() * 100)
                print(f"📊 Upload progress: {percent}%")

        video_id = response["id"]

        print("✅ Upload completed")
        print("🎥 Video ID:", video_id)
        print("🔗 URL:", f"https://youtube.com/watch?v={video_id}")
        print("==============================\n")

        return jsonify({
            "status": "uploaded",
            "videoId": video_id,
            "url": f"https://youtube.com/watch?v={video_id}"
        })

    except Exception as e:

        print("\n❌ YOUTUBE UPLOAD FAILED")
        print("Error:", str(e))

        import traceback
        traceback.print_exc()

        return jsonify({
            "status": "error",
            "message": str(e)
        })

@app.route("/")
def home():

    return render_template_string("""

<html>

<head>

<title>AI News Video Generator</title>

<style>
<style>

body{
  font-family: 'Segoe UI', Arial, sans-serif;
  background: linear-gradient(135deg,#eef2f7,#e6ecf5);
  margin:0;
}

/* CONTAINER */
.container{
  max-width:1200px;
  margin:40px auto;
  background:white;
  padding:30px;
  border-radius:16px;
  box-shadow:0 15px 40px rgba(0,0,0,0.08);
}

/* HEADINGS */
h2{
  margin-bottom:20px;
  color:#222;
}

h3{
  margin-top:25px;
  margin-bottom:10px;
  color:#444;
}

/* SECTION CARDS */
.section{
  background:#f9fbff;
  padding:15px 20px;
  border-radius:12px;
  margin-top:15px;
  border:1px solid #eef2f7;
}

/* FLEX ROW */
.row{
  display:flex;
  flex-wrap:wrap;
  gap:15px;
  align-items:center;
}

/* INPUTS */
select, textarea, input[type="file"]{
  padding:10px;
  border-radius:8px;
  border:1px solid #d0d7e2;
  font-size:14px;
}

textarea{
  width:100%;
  min-height:120px;
}

/* BUTTON */
button{
  padding:10px 18px;
  background:linear-gradient(135deg,#28a745,#20c997);
  color:white;
  border:none;
  border-radius:8px;
  cursor:pointer;
  font-weight:500;
  transition:0.2s;
}

button:hover{
  transform:translateY(-1px);
  box-shadow:0 5px 15px rgba(0,0,0,0.1);
}

/* TABLE */
table{
  width:100%;
  border-collapse:collapse;
  margin-top:10px;
  border-radius:10px;
  overflow:hidden;
}

th{
  background:linear-gradient(135deg,#007bff,#0056d2);
  color:white;
  font-weight:500;
}

th,td{
  padding:10px;
  border-bottom:1px solid #eee;
}

tr:hover{
  background:#f5f8ff;
}

/* PROGRESS */
.progress{
  width:100%;
  background:#e5e9f2;
  height:20px;
  border-radius:10px;
  margin-top:20px;
  overflow:hidden;
}

.progress-bar{
  height:100%;
  width:0%;
  background:linear-gradient(90deg,#007bff,#00c6ff);
  transition:0.3s;
}

/* VIDEO */
video{
  width:180px;
  border-radius:8px;
  box-shadow:0 5px 10px rgba(0,0,0,0.1);
}

/* CHECKBOX */
label{
  display:flex;
  align-items:center;
  gap:6px;
}

/* POPUP */
.popup {
  display:none;
  position:fixed;
  top:0;
  left:0;
  width:100%;
  height:100%;
  background:rgba(0,0,0,0.7);
  justify-content:center;
  align-items:center;
}

.popup video{
  max-width:90vw;
  max-height:75vh;
}

/* IMAGES USED */
.imagesUsed{
  font-size:12px;
  color:#666;
  max-width:200px;
  word-wrap:break-word;
}

/* RESPONSIVE */
@media(max-width:768px){
  .row{
    flex-direction:column;
    align-items:stretch;
  }

  video{
    width:100%;
  }
}

</style>

</head>

<body>

<div class="container">

<h2>AI News Video Generator</h2>

<!-- 🔹 MODE -->
<h3>Select Mode</h3>
<select id="type" onchange="onTypeChange()">
  <option value="news">News</option>
  <option value="content">Content</option>
  <option value="story">Story</option>
</select>

<!-- 🔹 NEWS SETTINGS -->
<div id="newsSection" class="section">

  <div class="row">
    <div>
      <label>Number of Videos</label><br>
      <select id="count" onchange="loadNews()">
        <option value="1">1</option>
        <option value="2">2</option>
        <option value="3">3</option>
        <option value="5">5</option>
        <option value="10">10</option>
      </select>
    </div>

    <div>
      <label>Category</label><br>
      <select id="category" onchange="loadNews()">

    <option value="world">World</option>
    <option value="technology">Technology</option>
    <option value="business">Business</option>
    <option value="sports">Sports</option>
    <option value="science">Science</option>

    <option value="politics">Politics</option>
    <option value="economy">Economy</option>
    <option value="finance">Finance</option>
    <option value="health">Health</option>
    <option value="education">Education</option>
    <option value="environment">Environment</option>
    <option value="climate">Climate</option>

    <option value="entertainment">Entertainment</option>
    <option value="movies">Movies</option>
    <option value="music">Music</option>
    <option value="celebrity">Celebrity</option>

    <option value="gaming">Gaming</option>
    <option value="ai">AI</option>
    <option value="startups">Startups</option>

    <option value="crime">Crime</option>
    <option value="law">Law</option>
    <option value="military">Military</option>

    <option value="space">Space</option>
    <option value="innovation">Innovation</option>

  </select>

</div>

<!-- 🔹 CONTENT MODE -->
<div id="contentSection" class="section" style="display:none; margin-top:10px;">
  <h3>Enter Content</h3>
  <textarea id="contentText" style="width:100%;height:120px;"
    placeholder="Enter your content"></textarea>
</div>

<!-- 🔹 STORY MODE -->
<div id="storySection" class="section" style="display:none; margin-top:10px;">
  <h3>Upload Story Images</h3>
  <input type="file" id="storyImages" multiple accept="image/*" />
</div>

<br>

<!-- 🔹 COMMON SETTINGS -->
Format
<select id="format">
  <option value="short">YouTube Shorts</option>
  <option value="instagram">Instagram Reel</option>
  <option value="youtube">YouTube Normal</option>
</select>

<label style="margin-left:10px;">
  <input type="checkbox" id="aiOnly"> AI Images Only
</label>

<button onclick="generate()">Generate Videos</button>

<!-- 🔹 NEWS TABLE -->
<div id="newsTablesWrap">
  <h3>Fetched News</h3>
  <table id="newsTable"></table>

  <h3>Selected News</h3>
  <table id="selectedTable">
    <tr><th>#</th><th>News Title</th><th>Action</th></tr>
  </table>
</div>

<!-- 🔹 PROGRESS -->
<div class="progress">
  <div id="bar" class="progress-bar"></div>
</div>

<p id="status"></p>

<!-- 🔹 OUTPUT -->
<h3>Generated Videos</h3>
<table id="videoTable"></table>

</div>

<!-- 🔹 POPUP -->
<div id="popup" class="popup">
  <div style="background:white;padding:20px;position:relative;max-height:90vh;overflow:auto;">
    <button onclick="closePopup()" style="position:absolute;top:10px;right:10px;">✕</button>
    <video id="popupVideo" controls></video>
  </div>
</div>

<script>

let currentNews=[];
let selectedNews=[];


function onTypeChange(){

let type=document.getElementById("type").value;

document.getElementById("newsTable").style.display="none";
document.getElementById("selectedTable").style.display="none";
document.getElementById("contentSection").style.display="none";
document.getElementById("storySection").style.display="none";

if(type==="news"){
    document.getElementById("newsTable").style.display="table";
    document.getElementById("selectedTable").style.display="table";
}
else if(type==="content"){
    document.getElementById("contentSection").style.display="block";
}
else if(type==="story"){
    document.getElementById("storySection").style.display="block";
}
}

// ---------------- LOAD NEWS ----------------
function loadNews(){

let count=document.getElementById("count").value;
let category=document.getElementById("category").value;

fetch(`/news?count=${count}&category=${category}`)
.then(r=>r.json())
.then(d=>{

currentNews=d.news;

let table=document.getElementById("newsTable");
table.innerHTML="<tr><th>Select</th><th>#</th><th>News Title</th></tr>";

d.news.forEach((n,i)=>{
let row=table.insertRow();

let checkbox=document.createElement("input");
checkbox.type="checkbox";

// ✅ keep checked if already selected
checkbox.checked = selectedNews.includes(n);

checkbox.onchange=function(){
    toggleSelection(n, checkbox.checked);
};

row.insertCell(0).appendChild(checkbox);
row.insertCell(1).innerText=i+1;
row.insertCell(2).innerText=n;
});

});

}

// ---------------- TOGGLE SELECTION ----------------
function toggleSelection(news, isChecked){

let table=document.getElementById("selectedTable");

if(isChecked){

    if(!selectedNews.includes(news)){
        selectedNews.push(news);

        let row=table.insertRow();
        row.setAttribute("data-news", news);

        row.insertCell(0).innerText=selectedNews.length;
        row.insertCell(1).innerText=news;

        // 🔥 DELETE BUTTON
        let btn=document.createElement("button");
        btn.innerText="❌";
        btn.style.background="red";

        btn.onclick=function(){
            removeSelected(news);
        };

        row.insertCell(2).appendChild(btn);
    }

} else {
    removeSelected(news);
}

}

// ---------------- REMOVE SELECTED ----------------
function removeSelected(news){

// remove from array
selectedNews = selectedNews.filter(n => n !== news);

// remove from table
let rows = document.querySelectorAll("#selectedTable tr");

rows.forEach(r=>{
    if(r.getAttribute("data-news") === news){
        r.remove();
    }
});

// uncheck checkbox in main table
let checkboxes = document.querySelectorAll("#newsTable input[type='checkbox']");

checkboxes.forEach(cb=>{
    let row = cb.closest("tr");
    let text = row.cells[2].innerText;

    if(text === news){
        cb.checked = false;
    }
});

updateSelectedTable();
}

// ---------------- REINDEX ----------------
function updateSelectedTable(){

let table=document.getElementById("selectedTable");

let rows=table.querySelectorAll("tr");

rows.forEach((row,index)=>{
    if(index===0) return;
    row.cells[0].innerText=index;
});

}

// ---------------- GENERATE ----------------
function generate(){

let type=document.getElementById("type").value;
let format=document.getElementById("format").value;
let aiOnly=document.getElementById("aiOnly").checked;

// ---------------- NEWS ----------------
if(type==="news"){

    let newsToSend = selectedNews.length > 0 ? selectedNews : currentNews;

    fetch("/generate",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            type:"news",
            news:newsToSend,
            format:format,
            ai_only: aiOnly
        })
    });

}

// ---------------- CONTENT ----------------
else if(type==="content"){

    let content=document.getElementById("contentText").value;

    if(!content){
        alert("Please enter content");
        return;
    }

    fetch("/generate-content",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            type:"content",
            content:content,
            format:format
        })
    });

}

// ---------------- STORY ----------------
else if(type==="story"){

    let files=document.getElementById("storyImages").files;

    if(files.length===0){
        alert("Upload at least 1 image");
        return;
    }

    let formData=new FormData();

    for(let f of files){
        formData.append("images",f);
    }

    formData.append("format",format);

    fetch("/generate-story",{
        method:"POST",
        body:formData
    });

}

checkProgress();
}

// ---------------- PROGRESS ----------------
function checkProgress(){

let interval=setInterval(()=>{

fetch("/progress")
.then(r=>r.json())
.then(d=>{

document.getElementById("bar").style.width=d.progress+"%";
document.getElementById("status").innerText=d.status;

if(d.status==="completed"){

clearInterval(interval);

let table=document.getElementById("videoTable");

table.innerHTML=
"<tr>\
<th>#</th>\
<th>News</th>\
<th>Title</th>\
<th>Description</th>\
<th>Images Used</th>\
<th>Preview</th>\
<th>Folder</th>\
<th>Play</th>\
<th>Upload</th>\
</tr>";

d.videos.forEach((v,i)=>{

let row=table.insertRow();

row.insertCell(0).innerText=i+1;
row.insertCell(1).innerText=v.news;
row.insertCell(2).innerText=v.title;
row.insertCell(3).innerText=v.description;

row.insertCell(4).innerHTML=`<div class="imagesUsed">${v.images}</div>`;
row.insertCell(5).innerHTML=`<video src="/video?path=${v.video}"></video>`;
row.insertCell(6).innerHTML=`<button onclick="openFolder('${v.folder}')">Open</button>`;
row.insertCell(7).innerHTML=`<button onclick="playVideo('${v.video}')">Play</button>`;

let uploadCell=row.insertCell(8);
let btn=document.createElement("button");
btn.innerText="Upload";

btn.onclick=function(){
    btn.disabled=true;
    btn.innerText="Uploading...";
    uploadYoutube(v.video,v.title,v.description);
};

uploadCell.appendChild(btn);

});

}

});

},1500);
}

// ---------------- OTHER ----------------
function uploadYoutube(path,title,description){

fetch("/upload_youtube",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
video:path,
title:title,
description:description
})
})
.then(r=>r.json())
.then(d=>{
alert("Upload Status: "+d.status);
});

}

function playVideo(path){
document.getElementById("popup").style.display="flex";
document.getElementById("popupVideo").src="/video?path="+path;
}

function closePopup(){
document.getElementById("popup").style.display="none";
}

function openFolder(path){
fetch("/open_folder?path="+encodeURIComponent(path));
}

window.onload=loadNews;

</script>

</body>
</html>

""")

@app.route("/news")
def news():

    count = int(request.args.get("count",3))
    category = request.args.get("category","world")

    url = f"https://news.google.com/rss/search?q={category}&hl=en-US&gl=US&ceid=US:en"

    feed = feedparser.parse(url)

    news_list = []

    for entry in feed.entries:
        news_list.append(entry.title)

    return jsonify({"news": news_list[:count]})

def generate_videos_background(news_list, format_type, ai_only):

    progress_status["progress"] = 0
    progress_status["status"] = "Starting..."
    progress_status["videos"] = []

    total = len(news_list)

    for i, news in enumerate(news_list):

        progress_status["status"] = f"Generating video {i+1}/{total}"

        try:
            result = generate_news_short(news, format_type, ai_only)  # ✅ pass
            progress_status["videos"].append(result)

        except Exception as e:
            import traceback
            traceback.print_exc()

        progress_status["progress"] = int(((i+1)/total)*100)

    progress_status["status"] = "completed"



@app.route("/open_folder")
def open_folder():

    path = request.args.get("path")

    subprocess.Popen(["xdg-open", path])

    return "ok"



@app.route("/generate", methods=["POST"])
def generate():

    data = request.json

    news_list = data.get("news", [])
    format_type = data.get("format", "short")
    ai_only = data.get("ai_only", False)   # ✅ NEW

    thread = threading.Thread(
        target=generate_videos_background,
        args=(news_list, format_type, ai_only)  # ✅ pass
    )

    thread.start()

    return jsonify({"status":"started"})


@app.route("/video")
def video():

    path = request.args.get("path")

    return send_file(path, mimetype="video/mp4")

@app.route("/progress")
def progress():
    return jsonify(progress_status)


@app.route("/api/chat/start", methods=["POST"])
def api_chat_start():

    data = request.json
    user_id = data.get("user_id")

    if not user_id:
        return {"error": "user_id required"}, 400

    session = ChatSession(
        user_id=user_id
    )

    db.session.add(session)
    db.session.commit()

    return {
        "session_id": session.id
    }


# ------------------------------
# Run
# ------------------------------

if __name__ == "__main__":

        app.run(host="0.0.0.0", port=5000, debug=False)

##    news_list = get_trending_news()
##
##
##    for news in news_list:
##
##        generate_news_short(news)
