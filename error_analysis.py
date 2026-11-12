import streamlit as st
import requests
import base64
import json
import re
from PIL import Image
from io import BytesIO
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

# ===================== 配置区 =====================
# 自动从 Streamlit Secrets 读取密钥，确保安全
API_KEY = st.secrets["API_KEY"]
MODEL_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

# 【提速点1】换用速度极快、性价比极高的 Lite 模型
MODEL_NAME = "doubao-seed-2-1-lite-260915" 
# ==================================================

@st.cache_resource
def get_session():
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504], allowed_methods=["POST"])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=2)
    s.mount("https://", adapter)
    return s

SESSION = get_session()

def compress_image(raw_bytes, max_width=1080, quality=80):
    img = Image.open(BytesIO(raw_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()

# 【格式修复】确保公式能在 Streamlit 中完美渲染
def fix_markdown_format(text):
    # 1. 给数学函数和变量之间加空格
    text = re.sub(r'\\(ln|sin|cos|tan)([0-9a-zA-Z])', r'\\\1 \2', text)
    # 2. 强行把 $$...$$ 拆成独立的三行
    text = re.sub(r'\$\$(.*?)\$\$', r'\n$$\n\1\n$$\n', text, flags=re.DOTALL)
    # 3. 清理多余的空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text

def analyze_with_stream(b64_data, status_text, result_placeholder):
    # 【提速点2】极度精简的 Prompt，直接要求“极简推导”，拒绝废话
    prompt = """
你是极度高效的高数及全学科批改助手。请用最精炼的语言输出。

【排版要求】
1. 公式必须用 `$$` 独立成行或 `$` 包裹，变量严格使用英文。
2. 【正确推导】只需写出最核心的关键步骤（不要超过4步），严禁长篇大论。

【输出结构】
先判断结果是否正确。
- 如果正确：【结论】解答正确。【标准推导】极简推导。
- 如果有错误：【错误点】一句话指出错误。【正确推导】极简推导。
"""
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64_data}
                    }
                ]
            }
        ],
        "max_tokens": 1000,  # 【提速点3】严格限制生成长度，从 2048 降到 1000
        "stream": True,
    }
    headers = {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json; charset=utf-8",
    }

    full_text = ""
    finish_reason = None
    try:
        res = SESSION.post(
            MODEL_URL,
            headers=headers,
            json=payload,
            timeout=(5, 120),
            verify=False,
            stream=True,
        )
        if res.status_code != 200:
            status_text.error("API 返回异常，状态码：" + str(res.status_code))
            return None

        for line in res.iter_lines():
            if not line:
                continue
            line_str = line.decode('utf-8', errors='ignore').strip()
            if not line_str.startswith("data: "):
                continue
            chunk = line_str[6:].strip()
            if chunk == "[DONE]":
                break
            try:
                data = json.loads(chunk)
                delta = data["choices"][0]["delta"]
                finish_reason = data["choices"][0].get("finish_reason")
                
                content = delta.get("content")
                if content:
                    full_text += content
                    # 流式打字阶段：去掉符号直接显示纯文本，防止中途渲染崩溃
                    safe_text = full_text.replace("$", "").replace("\\", "")
                    result_placeholder.markdown(safe_text)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        # 【最终渲染】修复格式并渲染完美公式
        final_text = fix_markdown_format(full_text)
        
        result_placeholder.empty()
        result_placeholder.markdown(final_text)
        
        # 如果因为字数限制被截断了，给用户一个提示
        if finish_reason == "length":
            st.warning("⚠️ 注意：这道题非常复杂，输出达到了长度上限，后半部分可能被截断。建议分次拍照上传。")
            
        return final_text

    except requests.exceptions.ConnectTimeout:
        status_text.error("连接超时：网络出口受限，请检查网络")
    except requests.exceptions.ReadTimeout:
        status_text.error("读取超时：模型处理时间超过 120 秒")
    except Exception as e:
        status_text.error("请求异常：" + str(e))
    return None

# ================= 页面 UI =================
st.title("全学科错题识别与归因分析")
upload_file = st.file_uploader("上传错题照片", type=["png", "jpg", "jpeg"])

if upload_file is not None:
    st.image(upload_file, caption="上传的错题", width="stretch")
    file_bytes = upload_file.read()

    if st.button("开始分析", type="primary"):
        compressed_bytes = compress_image(file_bytes)
        b64_data = base64.b64encode(compressed_bytes).decode("utf-8")

        status_text = st.empty() 
        status_text.info("极速分析中，请稍候...")
        
        result_placeholder = st.empty() 
        
        result = analyze_with_stream(b64_data, status_text, result_placeholder)
        
        if result:
            status_text.success("分析完成")
