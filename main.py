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
from fastapi.middleware.gzip import GZipMiddleware
from openai import OpenAI

from prompt import build_analysis_messages
from database import (
    init_db, save_analysis, get_history, get_trend, get_timeline,
    get_analysis_by_id, delete_analysis,
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
    import re
    from datetime import datetime, timedelta

    # 尝试从聊天记录中提取日期
    chat_date = None
    date_patterns = [
        r'(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})',  # 2024-03-15, 2024/3/15
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',        # 2024年3月15日
        r'(\d{1,2})月(\d{1,2})日',                  # 3月15日（年份用当前年）
    ]
    for pattern in date_patterns:
        match = re.search(pattern, chat_text)
        if match:
            groups = match.groups()
            try:
                if len(groups) == 3:
                    year, month, day = int(groups[0]), int(groups[1]), int(groups[2])
                    # 处理两位数年份
                    if year < 100:
                        year += 2000
                    chat_date = f"{year:04d}-{month:02d}-{day:02d}"
                elif len(groups) == 2:
                    month, day = int(groups[0]), int(groups[1])
                    chat_date = f"{datetime.now().year:04d}-{month:02d}-{day:02d}"
                break
            except (ValueError, IndexError):
                continue

    # 处理"昨天"""今天""上周"等相对时间
    if not chat_date:
        today = datetime.now().date()
        if '昨天' in chat_text or 'Yesterday' in chat_text:
            chat_date = str(today - timedelta(days=1))
        elif '前天' in chat_text:
            chat_date = str(today - timedelta(days=2))
        elif '今天' in chat_text or 'Today' in chat_text:
            chat_date = str(today)
        elif '上周' in chat_text:
            chat_date = str(today - timedelta(days=7))

    heart_rate = random.randint(55, 85)
    levels = ["有好感", "暖味期", "热恋期"]
    level = levels[min(heart_rate // 30, 2)]
    return {
        "heart_rate": heart_rate,
        "level": level,
        "chat_date": chat_date,
        "dimensions": {
            "initiative": {"score": random.randint(50, 90), "evidence": f"{crush_name} 主动发起聊天3次，包括分享今天的心情"},
            "response_quality": {"score": random.randint(55, 88), "evidence": f"平均回复字数60+，频繁使用表情包和语气词"},
            "emotional_temp": {"score": random.randint(45, 82), "evidence": f"大量使用'哈哈哈'和'呢'，语气轻松亲切"},
            "time_signals": {"score": random.randint(40, 78), "evidence": f"有深夜聊天记录，回复速度较快"},
            "exclusivity": {"score": random.randint(50, 92), "evidence": f"分享了日常生活细节，展现出信任感"}
        },
        "key_signals": [
            f"{crush_name} 在对话中使用了'我们'，这是心理距离缩短的信号",
            f"当你发'困了'时，{crush_name} 立刻关心你的状态，关心程度超过普通朋友",
            f"{crush_name} 主动提出了下次见面的建议，这是典型的好感信号"
        ],
        "advice": f"当前心动值 {heart_rate} 分，{crush_name} 对你有明显好感。建议下周找个自然的机会约出来见面，比如一起吃饭或看电影。注意不要过早表白，先享受曖昧期，让关系自然发展。",
        "risk_warning": ""
    }

app = FastAPI(title="心动解码器", version="0.1.0")

# 启用 gzip 压缩，静态文件传输更快
app.add_middleware(GZipMiddleware, minimum_size=500)

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


def is_timestamp(text: str) -> bool:
    """检测文本是否为时间戳格式（微信聊天截图中的日期/时间）"""
    import re
    text = text.strip()
    patterns = [
        r'\d{1,2}:\d{2}',                           # 20:32, 8:30
        r'\d{4}-\d{2}-\d{2}',                        # 2024-01-15
        r'\d{2}/\d{2}/\d{2,4}',                      # 01/15/24
        r'\d+\u6708\d+\u65e5',                        # 1月15日
        r'\d+\u70b9\d+\u5206',                        # 8点30分
        r'\d{1,2}\s*(?:AM|PM|am|pm)',               # 8:30 AM
        r'\u6628\u5929|\u4eca\u5929|\u660e\u5929|\u6628\u665a|\u4eca\u665a|\u6628\u65e9|\u4eca\u65e9',
        r'(?:Yesterday|Today|Tomorrow|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)',
        r'[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?',  # January 15
    ]
    for p in patterns:
        if re.search(p, text):
            return True
    time_words = ['yesterday', 'today', 'tomorrow', '昨天', '今天', '明天']
    if text.lower() in time_words:
        return True
    return False


def do_ocr(image_path: Path) -> str:
    """
    使用 pytesseract 进行 OCR，支持微信聊天截图的智能分栏识别
    核心逻辑：基于气泡边界间隙 + 颜色检测，而非单纯文本块位置
    """
    try:
        from PIL import Image
        import pytesseract
        import numpy as np

        img = Image.open(str(image_path))
        img_width = img.width
        img_height = img.height
        center_x = img_width // 2

        # 转换为 numpy 数组用于颜色分析
        img_array = np.array(img)

        # 使用 image_to_data 获取每个文本块的位置信息
        data = pytesseract.image_to_data(
            str(image_path),
            lang="chi_sim+eng",
            output_type=pytesseract.Output.DICT
        )

        # ========== 第一步：收集有效文本块 ==========
        blocks = []
        n_boxes = len(data['text'])
        for i in range(n_boxes):
            text = data['text'][i].strip()
            conf = int(data['conf'][i])
            if not text or conf < 25:
                continue

            x = data['left'][i]
            y = data['top'][i]
            w = data['width'][i]
            h = data['height'][i]

            if w < 10 or h < 10:
                continue

            # 计算边界间隙（关键特征）
            left_gap = x
            right_gap = img_width - (x + w)
            text_center = x + w // 2

            # 颜色分析：在文本块周围采样背景色
            color_hint = detect_bubble_color(img_array, x, y, w, h)

            blocks.append({
                'text': text,
                'x': x, 'y': y, 'w': w, 'h': h,
                'left_gap': left_gap,
                'right_gap': right_gap,
                'center': text_center,
                'color_hint': color_hint,
                'conf': conf
            })

        if not blocks:
            # 回退到简单 OCR
            text = pytesseract.image_to_string(str(image_path), lang="chi_sim+eng")
            return text.strip()

        # ========== 区域过滤：去掉顶部和底部非聊天区域 ==========
        # 微信截图布局分层：
        #   - 状态栏：y < 8%（时间、电量、信号）→ 直接过滤
        #   - 标题栏：8% ~ 12%（返回按钮、备注昵称）→ 检测备注特征（居中、短文本）
        #   - 聊天区：12% ~ 90%（消息气泡）→ 保留
        #   - 底部：> 90%（输入框、导航）→ 过滤
        status_bar_cutoff = img_height * 0.08    # 状态栏
        title_bar_cutoff = img_height * 0.12     # 标题栏上限
        bottom_cutoff = img_height * 0.90        # 底部

        filtered_blocks = []
        for b in blocks:
            y = b['y']

            # 过滤状态栏
            if y < status_bar_cutoff:
                continue

            # 过滤底部
            if y > bottom_cutoff:
                continue

            # 标题栏区域：检测备注特征（居中、短文本）
            if status_bar_cutoff <= y <= title_bar_cutoff:
                text = b['text']
                center = b['center']
                # 备注特征：居中且短文本（少于 5 个字符）
                is_centered = abs(center - img_width // 2) < img_width * 0.15
                is_short = len(text) <= 5
                if is_centered and is_short:
                    continue  # 这是备注，过滤掉

            # 其他情况保留
            filtered_blocks.append(b)

        # 如果过滤后太少，可能是非微信截图，保留原始块
        if len(filtered_blocks) >= 3:
            blocks = filtered_blocks

        # ========== 第二步：按 y 坐标分组为气泡 ==========
        blocks.sort(key=lambda b: b['y'])

        bubble_groups = []
        current_group = [blocks[0]]

        for block in blocks[1:]:
            last = current_group[-1]
            # 判断是否属于同一气泡：y距离近 或 边界特征相似
            y_gap = block['y'] - (last['y'] + last['h'])
            y_threshold = max(last['h'] * 2, 30)

            # 边界特征相似性：同一说话人的气泡边界模式应该一致
            edge_similar = (
                abs(block['left_gap'] - last['left_gap']) < img_width * 0.15 or
                abs(block['right_gap'] - last['right_gap']) < img_width * 0.15
            )

            if y_gap < y_threshold and edge_similar:
                current_group.append(block)
            else:
                bubble_groups.append(current_group)
                current_group = [block]

        bubble_groups.append(current_group)

        # ========== 第三步：判断每个气泡的说话人 ==========
        merged_lines = []
        for group in bubble_groups:
            if not group:
                continue

            # 检查是否是时间戳
            combined_text = ' '.join(b['text'] for b in group)
            if is_timestamp(combined_text):
                merged_lines.append({
                    'speaker': '[时间]',
                    'text': combined_text,
                    'y': group[0]['y']
                })
                continue

            # 分析整个气泡组的边界特征
            avg_left_gap = sum(b['left_gap'] for b in group) / len(group)
            avg_right_gap = sum(b['right_gap'] for b in group) / len(group)
            min_left = min(b['left_gap'] for b in group)
            min_right = min(b['right_gap'] for b in group)

            # 颜色投票
            color_votes = {'self': 0, 'other': 0, 'unknown': 0}
            for b in group:
                if b['color_hint'] == 'green':
                    color_votes['self'] += 1
                elif b['color_hint'] == 'white':
                    color_votes['other'] += 1
                else:
                    color_votes['unknown'] += 1

            # 综合判断
            # 判断 1：哪侧贴边？
            is_right_aligned = min_right < min_left * 0.6  # 右侧很贴边
            is_left_aligned = min_left < min_right * 0.6   # 左侧很贴边

            # 判断 2：平均间隙偏移
            gap_ratio = avg_left_gap / (avg_right_gap + 1)  # +1 避免除零

            # 综合得分
            self_score = 0
            other_score = 0

            if is_right_aligned:
                self_score += 3
            if is_left_aligned:
                other_score += 3
            if gap_ratio > 2.0:
                other_score += 2  # 左侧间隙大得多
            if gap_ratio < 0.5:
                self_score += 2   # 右侧间隙大得多
            if color_votes['self'] > color_votes['other']:
                self_score += 2
            if color_votes['other'] > color_votes['self']:
                other_score += 2

            # 中心位置作为辅助
            avg_center = sum(b['center'] for b in group) / len(group)
            if avg_center > center_x + img_width * 0.05:
                self_score += 1
            elif avg_center < center_x - img_width * 0.05:
                other_score += 1

            # 确定说话人
            if self_score > other_score:
                speaker = '[自己]'
            elif other_score > self_score:
                speaker = '[对方]'
            else:
                # 分不清，看中心位置
                speaker = '[自己]' if avg_center > center_x else '[对方]'

            # 合并文本（按行分组）
            group.sort(key=lambda b: b['y'])
            lines = []
            current_line = [group[0]]
            for b in group[1:]:
                if abs(b['y'] - current_line[-1]['y']) < max(b['h'], current_line[-1]['h']) * 1.2:
                    current_line.append(b)
                else:
                    current_line.sort(key=lambda x: x['x'])
                    lines.append(' '.join(x['text'] for x in current_line))
                    current_line = [b]
            current_line.sort(key=lambda x: x['x'])
            lines.append(' '.join(x['text'] for x in current_line))

            merged_lines.append({
                'speaker': speaker,
                'text': '\n'.join(lines),
                'y': group[0]['y']
            })

        # ========== 第四步：生成最终文本 ==========
        result_lines = []
        prev_speaker = None

        for line in merged_lines:
            if line['speaker'] == '[时间]':
                result_lines.append(f"\n--- {line['text']} ---")
                prev_speaker = None
            elif line['speaker'] == prev_speaker:
                result_lines[-1] += "\n" + line['text']
            else:
                result_lines.append(f"{line['speaker']} {line['text']}")
                prev_speaker = line['speaker']

        return '\n'.join(result_lines).strip()

    except Exception as e:
        import traceback
        print(f"OCR 增强模式失败: {e}")
        traceback.print_exc()
        try:
            import pytesseract
            return pytesseract.image_to_string(str(image_path), lang="chi_sim+eng").strip()
        except:
            return f"[OCR 失败: {str(e)}]"


def detect_bubble_color(img_array, x, y, w, h):
    """
    检测文本块周围的气泡颜色
    返回: 'green'(用户消息), 'white'(对方消息), 'unknown'
    """
    try:
        h_img, w_img = img_array.shape[:2]

        # 在文本块四周采样（气泡背景）
        samples = []
        for dy in [-h//2, h+2, h+8]:
            for dx in [-5, w//2, w+5]:
                sy = max(0, min(y + dy, h_img - 1))
                sx = max(0, min(x + dx, w_img - 1))
                samples.append(img_array[sy, sx])

        if not samples:
            return 'unknown'

        samples = np.array(samples)
        avg_color = np.mean(samples, axis=0)
        r, g, b = avg_color[:3]

        # 检测绿色气泡（微信默认绿色 #95EC69 或相似）
        # 绿色特征：G 明显高于 R 和 B
        if g > r + 20 and g > b + 20 and g > 100:
            return 'green'

        # 检测白色/浅灰色气泡
        # 白色特征：RGB 接近
        max_diff = max(abs(r-g), abs(g-b), abs(r-b))
        brightness = (r + g + b) / 3
        if max_diff < 30 and brightness > 180:
            return 'white'

        return 'unknown'
    except:
        return 'unknown'


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
        # 如果 AI 没有返回有效 chat_date，用 created_at 的日期部分作为 fallback
        chat_date = result.get("chat_date")
        if not chat_date or not isinstance(chat_date, str) or len(chat_date) != 10:
            chat_date = datetime.now().strftime("%Y-%m-%d")

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
            risk_warning=result.get("risk_warning", ""),
            chat_date=chat_date
        )
        result["analysis_id"] = analysis_id
        result["chat_date"] = chat_date

        return JSONResponse(result)

    except json.JSONDecodeError as e:
        return JSONResponse({"error": f"AI 返回格式错误: {str(e)}", "raw": content}, status_code=500)
    except Exception as e:
        result = generate_mock_result(crush_name, combined_text)
        chat_date = result.get("chat_date")
        if not chat_date:
            chat_date = datetime.now().strftime("%Y-%m-%d")
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
            risk_warning=result.get("risk_warning", ""),
            chat_date=chat_date
        )
        result["analysis_id"] = analysis_id
        result["chat_date"] = chat_date
        result["_note"] = f"API 暂时不可用，使用演示数据（原因: {str(e)}）"
        return JSONResponse(result)



@app.get("/history")
async def history(
    limit: int = 20,
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """获取当前用户的分析历史记录"""
    user = get_current_user(authorization)
    user_id = user["id"] if user else None
    guest_uid = x_guest_uid if not user else None

    if not user_id and not guest_uid:
        return JSONResponse({"error": "未登录"}, status_code=401)

    records = get_history(user_id=user_id, guest_uid=guest_uid, limit=limit)
    return records


@app.delete("/analysis/{analysis_id}")
async def delete_analysis_api(
    analysis_id: int,
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """删除一条历史分析记录"""
    user = get_current_user(authorization)
    user_id = user["id"] if user else None
    guest_uid = x_guest_uid if not user else None

    if not user_id and not guest_uid:
        return JSONResponse({"error": "未登录"}, status_code=401)

    # 验证记录存在且属于当前用户
    record = get_analysis_by_id(analysis_id)
    if not record:
        return JSONResponse({"error": "记录不存在"}, status_code=404)

    if user_id and record.get("user_id") != user_id:
        return JSONResponse({"error": "无权删除"}, status_code=403)
    if guest_uid and record.get("guest_uid") != guest_uid:
        return JSONResponse({"error": "无权删除"}, status_code=403)

    success = delete_analysis(analysis_id, user_id=user_id, guest_uid=guest_uid)
    if success:
        return {"success": True, "message": "删除成功"}
    return JSONResponse({"error": "删除失败"}, status_code=500)


@app.get("/trend")
async def trend(
    crush_name: str,
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """获取某个 crush 的心动指数趋势（所有历史记录）"""
    user = get_current_user(authorization)
    user_id = user["id"] if user else None
    guest_uid = x_guest_uid if not user else None

    if not crush_name:
        return JSONResponse({"error": "请提供 crush 名称"}, status_code=400)

    if not user_id and not guest_uid:
        return JSONResponse({"error": "未登录"}, status_code=401)

    records = get_trend(crush_name, user_id=user_id, guest_uid=guest_uid)
    return records


@app.get("/timeline")
async def timeline(
    crush_name: str,
    granularity: str = "day",
    authorization: str = Header(None),
    x_guest_uid: str = Header(None)
):
    """获取某个 crush 的心动指数时间轴（按天/周聚合）"""
    user = get_current_user(authorization)
    user_id = user["id"] if user else None
    guest_uid = x_guest_uid if not user else None

    if not crush_name:
        return JSONResponse({"error": "请提供 crush 名称"}, status_code=400)

    if granularity not in ("day", "week"):
        granularity = "day"

    result = get_timeline(crush_name, user_id=user_id, guest_uid=guest_uid, granularity=granularity)
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
