from fastapi import FastAPI, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
import secrets
import os
import stripe
import json
from dotenv import load_dotenv

load_dotenv()

print("ENV FILE EXISTS:", os.path.exists(".env"))
print("CURRENT DIR:", os.getcwd())
print("ENV FILE ABSOLUTE PATH:", os.path.abspath(".env"))

app = FastAPI()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory="templates")

# Stripe configuration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
APP_URL = os.getenv("APP_URL", "http://127.0.0.1:8000")

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
print("DEBUG STRIPE KEY:", stripe.api_key)
print("DEBUG PRICE_ID:", STRIPE_PRICE_ID)
print("DEBUG SECRET:", STRIPE_SECRET_KEY)

# In-memory user store
users_db = {}

# Simple session store (in production, use proper session management)
sessions = {}


def create_session(username: str) -> str:
    """Create a new session for a user"""
    session_id = secrets.token_urlsafe(32)
    sessions[session_id] = {
        "username": username,
        "counts": {}
    }
    return session_id


def get_username_from_session(session_id: Optional[str]) -> Optional[str]:
    """Get username from session ID"""
    if session_id and session_id in sessions:
        session_data = sessions[session_id]
        if isinstance(session_data, dict):
            return session_data.get("username")
        # Backward compatibility for old format
        return session_data
    return None


def get_session_data(session_id: Optional[str]) -> Optional[dict]:
    """Get full session data"""
    if session_id and session_id in sessions:
        session_data = sessions[session_id]
        if isinstance(session_data, dict):
            return session_data
        # Backward compatibility - convert old format
        username = session_data
        sessions[session_id] = {
            "username": username,
            "counts": {}
        }
        return sessions[session_id]
    return None


def is_user_pro(username: Optional[str]) -> bool:
    """Check if user has pro status"""
    if username and username in users_db:
        return users_db[username].get("is_pro", False)
    return False


def increment_usage(session_id: Optional[str], tool_name: str) -> int:
    """Increment usage count for a tool and return current count"""
    if not session_id:
        return 0
    session_data = get_session_data(session_id)
    if session_data:
        if "counts" not in session_data:
            session_data["counts"] = {}
        if tool_name not in session_data["counts"]:
            session_data["counts"][tool_name] = 0
        session_data["counts"][tool_name] += 1
        return session_data["counts"][tool_name]
    return 0


def get_usage_count(session_id: Optional[str], tool_name: str) -> int:
    """Get current usage count for a tool"""
    session_data = get_session_data(session_id)
    if session_data and "counts" in session_data:
        return session_data["counts"].get(tool_name, 0)
    return 0


def check_usage_limit(request: Request, username: str, tool_name: str) -> Optional[HTMLResponse]:
    """Check if user has reached usage limit. Returns error template if limit reached, None otherwise"""
    if is_user_pro(username):
        return None  # Pro users have unlimited usage
    
    session_id = request.cookies.get("session_id")
    usage_count = get_usage_count(session_id, tool_name)
    
    if usage_count >= 3:
        return templates.TemplateResponse("limit_reached.html", {
            "request": request,
            "username": username,
            "tool_name": tool_name
        })
    
    # Increment usage (only if limit not reached)
    increment_usage(session_id, tool_name)
    return None


def delete_session(session_id: Optional[str]):
    """Delete a session"""
    if session_id and session_id in sessions:
        del sessions[session_id]


def get_current_username(request: Request) -> Optional[str]:
    """Get current username from request cookies"""
    session_id = request.cookies.get("session_id")
    return get_username_from_session(session_id)


def require_login(request: Request):
    """
    Require user to be logged in.
    Returns username if logged in, RedirectResponse to "/" if not.
    """
    username = get_current_username(request)
    if not username:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return username


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Public landing page"""
    # Check if user is logged in (optional - for conditional display)
    username = get_current_username(request)
    is_pro = is_user_pro(username) if username else False
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "username": username,
        "is_pro": is_pro
    })


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, error: Optional[str] = None):
    """Show login form"""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error
    })


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handle login submission"""
    # Check if user exists and password matches
    if username in users_db and users_db[username]["password"] == password:
        # Create session
        session_id = create_session(username)
        # Redirect to dashboard with session cookie
        response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
        response.set_cookie(key="session_id", value=session_id, httponly=True)
        return response
    else:
        # Show error
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Invalid username or password"
        })


@app.get("/register", response_class=HTMLResponse)
async def register_get(request: Request, error: Optional[str] = None):
    """Show register form"""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": error
    })


@app.post("/register")
async def register_post(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handle registration"""
    # Check if user already exists
    if username in users_db:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Username already exists"
        })
    
    # Create new user
    users_db[username] = {
        "password": password,
        "is_pro": False
    }
    
    # Redirect to login
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_get(request: Request):
    """Show dashboard (protected)"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result

    is_pro = is_user_pro(username)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "username": username,
        "is_pro": is_pro
    })
@app.get("/tool/sora", response_class=HTMLResponse)
async def sora_tool(request: Request):
    """Sora Prompt Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_sora.html", {
        "request": request,
        "username": username
    })

@app.post("/tool/sora", response_class=HTMLResponse)
async def sora_tool_generate(request: Request, video_idea: str = Form(...)):
    """Handle Sora prompt generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "sora")
    if limit_check:
        return limit_check
    
    # Generate professional Sora prompt with structured sections
    generated_prompt = f"""Here is your optimized Sora prompt:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Main Idea:
• {video_idea}
• Cinematic storytelling with physically accurate motion

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Visual Style:
• Hyper-realistic textures and materials
• Rich depth of field with natural bokeh
• 4K, HDR-quality detail
• Cinematic color grading

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Camera & Motion:
• Dynamic tracking shots with smooth camera movement
• Physically accurate motion blur
• Professional cinematography techniques
• No slow motion unless explicitly requested

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lighting:
• Natural or stylized lighting that matches the mood
• Soft diffused shadows with realistic falloff
• HDR highlights with detail preservation
• Atmospheric lighting design

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Environment & Atmosphere:
• Detailed, immersive environment
• Realistic physics and interactions
• Rich atmospheric elements
• Spatial depth and dimension

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Character & Action:
• Natural character movement and expressions
• High-energy, dynamic actions
• Realistic body mechanics
• Expressive, believable performance

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Technical Notes:
• Suitable for Sora text-to-video generation
• Optimized for cinematic output
• Ready to copy directly into Sora interface"""

    return templates.TemplateResponse(
        "tool_sora.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )

@app.get("/logout", response_class=RedirectResponse)
async def logout(request: Request):
    """Handle logout"""
    session_id = request.cookies.get("session_id")
    delete_session(session_id)
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie(key="session_id")
    return response


# Tool routes (protected)
@app.get("/tool/runway", response_class=HTMLResponse)
async def tool_runway(request: Request):
    """Runway Prompt Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_runway.html", {
        "request": request,
        "generated_prompt": None,
        "video_idea": "",
        "username": username
    })

@app.post("/tool/runway", response_class=HTMLResponse)
async def tool_runway_generate(request: Request, video_idea: str = Form(...)):
    """Handle Runway prompt generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "runway")
    if limit_check:
        return limit_check
    
    # Generate professional Runway prompt with stylized focus
    generated_prompt = f"""Here is your optimized Runway prompt:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Main Idea:
• {video_idea}
• Optimized for Runway Gen-2/Gen-3 text-to-video

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Style & Look:
• Stylized, visually striking aesthetic
• Bold visual choices with strong personality
• Edit-friendly composition
• Artistic, creative direction

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Camera & Framing:
• Dynamic cinematic framing
• Smooth, professional camera movement
• Creative angles that enhance the narrative
• Clear subject focus and composition

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Motion & Timing:
• Natural motion that feels organic
• Well-paced action and movement
• Smooth transitions between moments
• Appropriate tempo for the concept

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Shot Style:
• Professional shot composition
• Balanced framing with visual interest
• Strong foreground/background separation
• Vivid, impactful visuals

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Lighting & Color:
• Dramatic contrast and depth
• Stylized highlights that create mood
• Rich color palette
• Atmospheric lighting design

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Extra Detail:
• Clear subject focus throughout
• Strong composition with visual hierarchy
• Details that enhance the storytelling
• Ready for Runway text-to-video generation"""

    return templates.TemplateResponse(
        "tool_runway.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )


@app.get("/tool/pika", response_class=HTMLResponse)
async def tool_pika(request: Request):
    """Pika Prompt Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_pika.html", {
        "request": request,
        "generated_prompt": None,
        "video_idea": "",
        "username": username
    })

@app.post("/tool/pika", response_class=HTMLResponse)
async def tool_pika_generate(request: Request, video_idea: str = Form(...)):
    """Handle Pika prompt generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "pika")
    if limit_check:
        return limit_check
    
    # Generate professional Pika prompt with animation/character focus
    generated_prompt = f"""Here is your optimized Pika prompt:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Main Idea:
• {video_idea}
• Dynamic animation optimized for Pika Labs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Character & Personality:
• Expressive, energetic character design
• Clear personality traits and emotions
• Dynamic character movement
• Engaging character presence

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Animation Style:
• Exaggerated motion for maximum impact
• Cartoon-like timing and rhythm
• Smooth, fluid animation
• Expressive facial animation and body language

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Camera & Movement:
• Dynamic camera movement with energy
• Energetic framing that captures action
• Engaging angles that enhance animation
• Smooth tracking and motion

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Motion & Energy:
• High-energy, fast-paced action
• Exaggerated motion for visual impact
• Fluid transitions between actions
• Dynamic timing and pacing

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Details & Effects:
• Vivid colors and stylized highlights
• Animated realism with personality
• Visual effects that enhance the animation
• Polished, professional animation quality

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Technical Notes:
• Perfect for short, dynamic loops
• Optimized for Pika Labs video generation
• Great for character-driven content
• Ready to copy directly into Pika interface"""

    return templates.TemplateResponse(
        "tool_pika.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )


@app.get("/tool/scene", response_class=HTMLResponse)
async def tool_scene(request: Request):
    """Scene Breakdown Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_scene.html", {
        "request": request,
        "generated_prompt": None,
        "video_idea": "",
        "username": username
    })

@app.post("/tool/scene", response_class=HTMLResponse)
async def tool_scene_generate(request: Request, video_idea: str = Form(...)):
    """Handle Scene Breakdown generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "scene")
    if limit_check:
        return limit_check
    
    # Generate scene breakdown prompt
    generated_prompt = f"""Scene Breakdown for: {video_idea}

**Scene 1 – Establishing Shot:**  
Wide angle showing the environment and setting the mood. Introduce the location, time of day, and overall atmosphere that frames the narrative.

**Scene 2 – Main Action:**  
The primary character enters and begins the core action. Focus on movement, energy, and the driving force of the scene.

**Scene 3 – Conflict / Twist:**  
A complication or unexpected element emerges. This could be an obstacle, revelation, or escalation that adds tension and depth.

**Scene 4 – Resolution:**  
The scene reaches its conclusion with a clear outcome. This could be a resolution, cliffhanger, or transition that sets up the next sequence.
"""

    return templates.TemplateResponse(
        "tool_scene.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )


@app.get("/tool/thumbnail", response_class=HTMLResponse)
async def tool_thumbnail(request: Request):
    """Thumbnail Prompt Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_thumbnail.html", {
        "request": request,
        "generated_prompt": None,
        "video_idea": "",
        "username": username
    })

@app.post("/tool/thumbnail", response_class=HTMLResponse)
async def tool_thumbnail_generate(request: Request, video_idea: str = Form(...)):
    """Handle Thumbnail prompt generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "thumbnail")
    if limit_check:
        return limit_check
    
    # Generate professional thumbnail prompt with static composition focus
    generated_prompt = f"""Here is your optimized thumbnail prompt:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Concept:
• {video_idea}
• High-conversion thumbnail optimized for YouTube, TikTok, and Reels

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Subject & Pose:
• Clear, dominant subject in the foreground
• Dynamic pose that conveys action or emotion
• Compelling body language
• Strong focal point that draws the eye

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Facial Expression:
• Strong, bold emotion (shock, excitement, curiosity)
• Expressive facial features that capture attention
• Eye contact with the viewer when appropriate
• Genuine, authentic expression

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Camera Angle & Composition:
• Dramatic close-up or medium shot
• Dynamic camera angle (low angle, Dutch angle, or eye-level)
• Rule of thirds composition
• Strong visual hierarchy

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Background & Colors:
• High-contrast background that makes subject pop
• Vibrant, eye-catching color palette
• Uncluttered background that doesn't distract
• Bold color contrast for maximum visibility

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Text & Overlay Suggestions:
• Bright, readable text overlay with strong contrast
• Bold typography that stands out
• Strategic text placement that doesn't block key elements
• Catchy headline or hook text
• Clear, concise messaging

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Visual Impact:
• High-contrast lighting for dramatic effect
• Sharp focus on the subject
• Professional, polished appearance
• Maximum click-through rate (CTR) optimization

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Technical Notes:
• Optimized for thumbnail generation with image models
• Perfect aspect ratio for YouTube, TikTok, Reels
• High-resolution quality for crisp detail
• Ready to use with AI image generation tools"""

    return templates.TemplateResponse(
        "tool_thumbnail.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )


@app.get("/tool/viral", response_class=HTMLResponse)
async def tool_viral(request: Request):
    """Viral Video Idea Generator tool - GET"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("tool_viral.html", {
        "request": request,
        "generated_prompt": None,
        "video_idea": "",
        "username": username
    })

@app.post("/tool/viral", response_class=HTMLResponse)
async def tool_viral_generate(request: Request, video_idea: str = Form(...)):
    """Handle Viral Video Idea generation - POST"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check usage limit
    limit_check = check_usage_limit(request, username, "viral")
    if limit_check:
        return limit_check
    
    # Generate professional viral video prompt with short-form platform focus
    generated_prompt = f"""Here is your optimized viral video concept:

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Core Concept:
• {video_idea}
• Optimized for TikTok, Reels, and Shorts platforms

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Hook (First 1-3 Seconds):
• Instant grab that stops scrolling immediately
• Shocking, surprising, or intriguing opening moment
• Visual or audio element that commands attention
• Clear promise of what's to come
• MUST capture attention in under 3 seconds

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Main Action (Seconds 3-8):
• High-energy, engaging core content
• Surprising or chaotic moment that maintains interest
• Clear narrative or visual progression
• Strong pacing that keeps viewers watching
• Builds on the hook's promise

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Escalation (Seconds 8-12):
• Dramatic unexpected event or reveal
• Increased intensity or surprise factor
• Moment that boosts retention significantly
• Twist that exceeds viewer expectations
• Escalation that prevents scrolling away

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Ending / Payoff (Final 2-3 Seconds):
• Satisfying resolution or punchline
• Moment that encourages shares and comments
• Clear call-to-action or memorable conclusion
• Leave viewers wanting to engage
• Optimized for replay value

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pacing & Beat:
• Fast-paced editing with quick cuts
• Rhythm that matches trending audio or beats
• No dead air or slow moments
• Consistent energy throughout
• Optimized for short attention spans

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Platform Optimization:
• Perfect for TikTok, Instagram Reels, YouTube Shorts
• Vertical format (9:16 aspect ratio)
• Designed for maximum engagement
• Shareable and comment-worthy content
• Viral potential with trending elements

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Shot Structure:
• Opening: Extreme close-up or wide reveal
• Middle: Dynamic action shots with movement
• Climax: Most impactful moment with full framing
• Ending: Memorable final shot with clear resolution

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Optional Extra Shots:
• Reaction shots for comedic or dramatic effect
• B-roll that adds context or energy
• Establishing shots that set the scene quickly
• Transition shots that maintain flow"""

    return templates.TemplateResponse(
        "tool_viral.html",
        {
            "request": request,
            "generated_prompt": generated_prompt,
            "video_idea": video_idea,
            "username": username
        }
    )


# Stripe Checkout Routes
@app.get("/upgrade", response_class=HTMLResponse)
async def upgrade_page(request: Request):
    """Pricing page - public access"""
    # Optional: check if user is logged in (for conditional display)
    username = get_current_username(request)
    is_pro = is_user_pro(username) if username else False
    
    return templates.TemplateResponse("upgrade.html", {
        "request": request,
        "username": username,
        "is_pro": is_pro
    })


@app.post("/create-checkout-session")
async def create_checkout_session(request: Request):
    """Create Stripe checkout session"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    # Check if PRICE_ID is missing
    price_id = os.getenv("STRIPE_PRICE_ID")
    if price_id is None:
        return JSONResponse({
            "error": "PRICE_ID is missing in environment"
        }, status_code=500)
    
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            success_url="http://127.0.0.1:8000/success",
            cancel_url="http://127.0.0.1:8000/cancel",
        )
        
        return JSONResponse({
            "checkout_url": session.url
        })
    except Exception as e:
        # Return full Stripe error details
        error_details = {
            "error": str(e),
            "error_type": type(e).__name__
        }
        if hasattr(e, 'user_message'):
            error_details["user_message"] = e.user_message
        if hasattr(e, 'code'):
            error_details["code"] = e.code
        
        return JSONResponse(error_details, status_code=500)


@app.get("/success", response_class=HTMLResponse)
async def success_page(request: Request):
    """Success page after Stripe checkout"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("success.html", {
        "request": request,
        "username": username
    })


@app.get("/cancel", response_class=HTMLResponse)
async def cancel_page(request: Request):
    """Cancel page after Stripe checkout"""
    auth_result = require_login(request)
    if isinstance(auth_result, RedirectResponse):
        return auth_result
    username = auth_result
    
    return templates.TemplateResponse("cancel.html", {
        "request": request,
        "username": username
    })


@app.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return JSONResponse({"error": "Invalid payload"}, status_code=400)
    except stripe.error.SignatureVerificationError:
        return JSONResponse({"error": "Invalid signature"}, status_code=400)
    
    # Handle the event
    if event.type == "checkout.session.completed":
        session = event.data.object
        customer_email = session.get("customer_email") or session.get("customer_details", {}).get("email")
        
        if customer_email and customer_email in users_db:
            users_db[customer_email]["is_pro"] = True
    
    return JSONResponse({"status": "success"})
