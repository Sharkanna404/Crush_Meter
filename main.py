"""
心动解码器 - FastAPI 后端
Crush Meter Backend
"""

import json
import os
import yaml
import bcrypt
import jwt
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Header, Request, File, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from openai import OpenAI

from prompt import build_analysis_messages
from database import (
    init_db, save_analysis, get_history, get_trend,
    create_user, get_user_by_username, get_user_by_id,
    generate_guest_uid, migrate_guest_to_user,
    save_image, get_images, get_image_by_id, delete_image,
    update_image_ocr, update_image_order, clear_user_images
)

# JWT 配置
JWT_SECRET = os.getenv("JWT_SECRET", "heart_decoder_secret_2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30

# 认证工具函数
def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def get_current_user(authorization: str = Header(None)) -> dict:
    """从 Authorization header 解析用户，返回 {user_id, username} 或 None"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    payload = decode_token(token)
    if not payload:
        return None
    user = get_user_by_id(int(payload["sub"]))
    return user

# 初始化数据库
init_db()

# 读取 API 配置
CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"

# 优先使用 DeepSeek（MVP 阶段成本最低）
api_key = os.getenv("DEEPSEEK_API_KEY", "")
base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
model_name = "deepseek-chat"

# 尝试从本地 .api_key 文件读取（用于测试，不提交到 git）
API_KEY_FILE = Path(__file__).parent / ".api_key"
if not api_key and API_KEY_FILE.exists():
    api_key = API_KEY_FILE.read_text().strip()

# 如果环境变量没有，尝试从 config 读取其他配置
if not api_key and CONFIG_PATH.exists():
    try:
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        # 尝试 moonshot_domestic 作为 fallback
        if config and "providers" in config and "moonshot_domestic" in config["providers"]:
            api_key = config["providers"]["moonshot_domestic"].get("api_key", "")
            base_url = config["providers"]["moonshot_domestic"].get("base_url", base_url)
            model_name = config["providers"]["moonshot_domestic"].get("model", model_name)
    except Exception:
        pass

# 模拟模式（API 不可用时使用测试数据）
MOCK_MODE = os.getenv("HEART_DECODER_MOCK", "false").lower() == "true"


def generate_mock_result(crush_name: str, chat_text: str) -> dict:
    """生成模拟分析结果（用于 API 不可用时的演示）"""
    import random
    heart_rate = random.randint(55, 85)
    levels = ["有好感", "暖味期", "热恋期"]
    level = levels[min(heart_rate // 30, 2)]
    return {
        "heart_rate": heart_rate,
        "level": level,
        "dimensions": {
            "initiative": {"score": random.randint(50, 90), "evidence": f"{crush_name} 主动发起聊天3次，包括分享今天的心情"},
            "response_quality": {"score": random.randint(55, 88), "evidence": f"平均回复字数60+，频繁使用表情包和语气词"},
            "emotional_temp": {"score": random.randint(45, 82), "evidence": f"大量使用‘哈哈哈’和‘呢’，语气轻松亲切"},
            "time_signals": {"score": random.randint(40, 78), "evidence": f"有深夜聊天记录，回复速度较快"},
            "exclusivity": {"score": random.randint(50, 92), "evidence": f"分享了日常生活细节，展现出信任感"}
        },
        "key_signals": [
            f"{crush_name} 在对话中使用了‘我们’，这是心理距离缩短的信号",
            f"当你发‘困了’时，{crush_name} 立刻关心你的状态，关心程度超过普通朋友",
            f"{crush_name} 主动提出了下次见面的建议，这是典型的好感信号"
        ],
        "advice": f"当前心动值 {heart_rate} 分，{crush_name} 对你有明显好感。建议下周找个自然的机会约出来见面，比如一起吃饭或看电影。注意不要过早表白，先享受曖昧期，让关系自然发展。",
        "risk_warning": ""
    }

app = FastAPI(title="心动解码器", version="0.1.0")

# 图片上传配置
UPLOAD_DIR = Path(__file__).parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
MAX_IMAGES = 10
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}

# 挂载静态文件
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---- OCR 工具函数 ----

def _resolve_user(auth: str, guest: str) -> tuple:
    """解析认证信息，返回 (user_id, guest_uid)"""
    user = get_current_user(auth)
    if user:
        return user["id"], None
    return None, guest


def do_ocr(image_path: Path) -> str:
    """使用 pytesseract 进行 OCR"""
    try:
        from PIL import Image
        import pytesseract
        # 中文+英文识别
        text = pytesseract.image_to_string(
            str(image_path),
            lang="chi_sim+eng"
        )
        return text.strip()
    except Exception as e:
        return f"[OCR 失败: {str(e)}]"


def validate_image(file: UploadFile) -> tuple:
    """验证图片格式和大小，返回 (ok, error_msg)"""
    if file.content_type not in ALLOWED_TYPES:
        return False, f"不支持的格式: {file.content_type}"
    # 注：实际大小检查在读取内容时进行
    return True, ""


def save_upload_file(file: UploadFile, user_id: int, guest_uid: str) -> dict:
    """保存上传的图片文件，返回图片信息 dict"""
    ext = Path(file.filename).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"

    unique_name = f"{uuid.uuid4().hex}{ext}"
    file_path = UPLOAD_DIR / unique_name

    # 保存文件
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 存入数据库
    image_id = save_image(
        filename=unique_name,
        original_name=file.filename,
        user_id=user_id,
        guest_uid=guest_uid
    )

    return {
        "id": image_id,
        "filename": unique_name,
        "original_name": file.filename,
        "url": f"/uploads/{unique_name}"
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


# ---- 用户系统 API ----

@app.post("/guest")
async def create_guest():
    """生成临时用户 UID"""
    return {"guest_uid": generate_guest_uid()}


@app.post("/register")
async def register(
    username: str = Form(...),
    password: str = Form(...),
    guest_uid: str = Form("")
):
    """注册新用户，可选择将临时数据迁移过来"""
    if len(username) < 2 or len(username) > 20:
        return JSONResponse({"error": "用户名需要2-20个字符"}, status_code=400)
    if len(password) < 4:
        return JSONResponse({"error": "密码至少需要4个字符"}, status_code=400)

    try:
        password_hash = hash_password(password)
        user_id = create_user(username, password_hash)

        # 迁移临时数据
        migrated = 0
        if guest_uid:
            migrated = migrate_guest_to_user(guest_uid, user_id)

        token = create_access_token(user_id)
        return {
            "token": token,
            "user": {"id": user_id, "username": username},
            "migrated": migrated
        }
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"注册失败: {str(e)}"}, status_code=500)


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...)):
    """登录"""
    user = get_user_by_username(username)
    if not user:
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)

    if not verify_password(password, user["password_hash"]):
        return JSONResponse({"error": "用户名或密码错误"}, status_code=401)

    token = create_access_token(user["id"])
    return {
        "token": token,
        "user": {"id": user["id"], "username": user["username"]}
    }


@app.get("/me")
async def me(authorization: str = Header(None)):
    """获取当前登录用户信息"""
    user = get_current_user(authorization)
    if not user:
        return JSONResponse({"error": "未登录"}, status_code=401)
    return {"user": user}


# ---- 图片/OCR API ----

@app.post("/upload_images")
async def upload_images(
    files: list[UploadFile] = File(...),
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """上传多张图片，返回图片信息列表"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)

    # 检查总数限制
    existing = get_images(user_id=user_id, guest_uid=guest_uid)
    if len(existing) + len(files) > MAX_IMAGES:
        return JSONResponse(
            {"error": f"最多只能上传 {MAX_IMAGES} 张图片，当前已有 {len(existing)} 张"},
            status_code=400
        )

    results = []
    errors = []

    for file in files:
        ok, msg = validate_image(file)
        if not ok:
            errors.append({"filename": file.filename, "error": msg})
            continue

        # 检查文件大小
        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            errors.append({"filename": file.filename, "error": "文件超过 5MB 限制"})
            continue

        # 重置文件指针
        await file.seek(0)

        try:
            info = save_upload_file(file, user_id, guest_uid)
            results.append(info)
        except Exception as e:
            errors.append({"filename": file.filename, "error": str(e)})

    return {
        "success": len(results),
        "failed": len(errors),
        "images": results,
        "errors": errors
    }


@app.get("/images")
async def list_images(authorization: str = Header(None), x_guest_uid: str = Header(None)):
    """获取当前用户的图片列表"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)
    images = get_images(user_id=user_id, guest_uid=guest_uid)
    # 添加 URL
    for img in images:
        img["url"] = f"/uploads/{img['filename']}"
    return images


@app.delete("/images/{image_id}")
async def delete_image_api(
    image_id: int,
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """删除图片"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)

    # 验证权限
    img = get_image_by_id(image_id)
    if not img:
        return JSONResponse({"error": "图片不存在"}, status_code=404)

    if user_id and img.get("user_id") != user_id:
        return JSONResponse({"error": "无权操作"}, status_code=403)
    if guest_uid and img.get("guest_uid") != guest_uid:
        return JSONResponse({"error": "无权操作"}, status_code=403)

    # 删除文件
    file_path = UPLOAD_DIR / img["filename"]
    if file_path.exists():
        file_path.unlink()

    # 删除数据库记录
    delete_image(image_id)
    return {"success": True}


@app.post("/images/{image_id}/ocr")
async def ocr_image(
    image_id: int,
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """对单张图片进行 OCR"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)

    img = get_image_by_id(image_id)
    if not img:
        return JSONResponse({"error": "图片不存在"}, status_code=404)

    # 权限验证
    if user_id and img.get("user_id") != user_id:
        return JSONResponse({"error": "无权操作"}, status_code=403)
    if guest_uid and img.get("guest_uid") != guest_uid:
        return JSONResponse({"error": "无权操作"}, status_code=403)

    # 更新状态为处理中
    update_image_ocr(image_id, "", status="processing")

    # 执行 OCR
    file_path = UPLOAD_DIR / img["filename"]
    if not file_path.exists():
        update_image_ocr(image_id, "[文件不存在]", status="error")
        return JSONResponse({"error": "文件不存在"}, status_code=404)

    text = do_ocr(file_path)
    status = "done" if not text.startswith("[OCR 失败") else "error"
    update_image_ocr(image_id, text, status=status)

    return {"image_id": image_id, "ocr_text": text, "status": status}


@app.put("/images/{image_id}/ocr")
async def update_ocr(
    image_id: int,
    ocr_text: str = Form(...),
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """编辑 OCR 结果"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)

    img = get_image_by_id(image_id)
    if not img:
        return JSONResponse({"error": "图片不存在"}, status_code=404)

    # 权限验证
    if user_id and img.get("user_id") != user_id:
        return JSONResponse({"error": "无权操作"}, status_code=403)
    if guest_uid and img.get("guest_uid") != guest_uid:
        return JSONResponse({"error": "无权操作"}, status_code=403)

    update_image_ocr(image_id, ocr_text, status="done")
    return {"success": True, "image_id": image_id}


@app.put("/images/reorder")
async def reorder_images(
    order: str = Form(...),  # JSON: [{"id": 1, "order": 0}, ...]
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """调整图片顺序"""
    user_id, guest_uid = _resolve_user(authorization, x_guest_uid)

    try:
        items = json.loads(order)
        for item in items:
            update_image_order(item["id"], item["order"])
        return {"success": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ---- 分析 API（支持 OCR 文本合并）----

@app.post("/analyze")
async def analyze(
    crush_name: str = Form(...),
    chat_text: str = Form(""),
    ocr_text: str = Form(""),
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    # 合并 OCR 文本和手动输入
    combined_text = chat_text.strip()
    if ocr_text.strip():
        if combined_text:
            combined_text += "\n\n" + ocr_text.strip()
        else:
            combined_text = ocr_text.strip()

    if not combined_text:
        return JSONResponse({"error": "聊天记录不能为空，请粘贴文字或上传图片 OCR"}, status_code=400)

    # 确定当前用户
    user = get_current_user(authorization)
    user_id = user["id"] if user else None
    guest_uid = x_guest_uid if not user else None

    messages = build_analysis_messages(crush_name, combined_text)

    try:
        if MOCK_MODE or not api_key:
            result = generate_mock_result(crush_name, combined_text)
        else:
            client = OpenAI(api_key=api_key, base_url=base_url)
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.3,
                max_tokens=3000
            )
            content = response.choices[0].message.content

            # 提取 JSON
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.startswith("```"):
                content = content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            result = json.loads(content)

        # 保存到数据库
        analysis_id = save_analysis(
            crush_name=crush_name,
            chat_preview=combined_text[:500],
            heart_rate=result.get("heart_rate", 50),
            level=result.get("level", "未知"),
            dimensions=result.get("dimensions", {}),
            key_signals=result.get("key_signals", []),
            advice=result.get("advice", ""),
            user_id=user_id,
            guest_uid=guest_uid,
            risk_warning=result.get("risk_warning", "")
        )
        result["analysis_id"] = analysis_id

        return JSONResponse(result)

    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"AI 返回格式错误: {str(e)}", "raw": content}, status_code=500)
    except Exception as e:
        result = generate_mock_result(crush_name, combined_text)
        analysis_id = save_analysis(
            crush_name=crush_name,
            chat_preview=combined_text[:500],
            heart_rate=result.get("heart_rate", 50),
            level=result.get("level", "未知"),
            dimensions=result.get("dimensions", {}),
            key_signals=result.get("key_signals", []),
            advice=result.get("advice", ""),
            user_id=user_id,
            guest_uid=guest_uid,
            risk_warning=result.get("risk_warning", "")
        )
        result["analysis_id"] = analysis_id
        result["_note"] = f"API 暂时不可用，使用演示数据（原因: {str(e)}）"
        return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
