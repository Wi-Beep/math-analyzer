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

# ===================== 只填你的API_KEY！ =====================
API_KEY = st.secrets["API_KEY"]
MODEL_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_NAME = "doubao-seed-2-1-pro-260915"
# ============================================================

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

# 【优雅格式化】只做换行修复和基础空格修复，不再乱替换文字
def fix_markdown_format(text):
    # 1. 给数学函数和变量之间加空格，防止 LaTeX 渲染错误（如 \ln2 变成 \ln 2）
    text = re.sub(r'\\(ln|sin|cos|tan)([0-9a-zA-Z])', r'\\\1 \2', text)
    
    # 2. 强行把 $$...$$ 拆成独立的三行，Streamlit 渲染公式的硬性要求
    text = re.sub(r'\$\$(.*?)\$\$', r'\n$$\n\1\n$$\n', text, flags=re.DOTALL)
    
    # 3. 清理多余的空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text

def analyze_with_stream(b64_data, status_text, result_placeholder):
    # 升级版的 Prompt：支持全学科与复杂大题自适应
    prompt = """
你是极其严谨的高数及全学科批改助手。你可以看图读取学生的解题步骤，请严格遵守以下规则：

【排版与格式】
1. 所有变量只能用英文字母，禁止用汉字代替。
2. 积分末尾请统一使用小写 `dx`、`dt`。
3. 【正确推导】必须分步骤书写。所有的独立公式必须用 `$$` 独立成行包裹；行内公式用 `$` 包裹。

【自适应输出规则（重要）】
- 如果题目很简单，请直接给出简洁的推导和结论。
- 如果题目包含多个小问，或者推导极其复杂，请务必详细展开，不要遗漏任何小问，必要时加上文字解释。

【判断与输出结构】
先判断学生的最终结果和核心逻辑是否正确（允许学生使用化简或跳过步骤，只要逻辑正确就不要强行找错）。
- 如果正确：【结论】学生解答完全正确。【标准推导】分步推导。
- 如果有错误：【错误点】一句话指出错误。【正确推导】分步推导。
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
        "max_tokens": 2048,  # 👈 这里已经帮你改成了 2048，防止大题被截断
        "stream": True,
    }
    headers = {
        "Authorization": "Bearer " + API_KEY,
        "Content-Type": "application/json; charset=utf-8",
    }

    full_text = ""
    finish_reason = None # 👈 用于检测是否被字数限制截断
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
                finish_reason = data["choices"][0].get("finish_reason") # 👈 记录结束原因
                
                content = delta.get("content")
                if content:
                    full_text += content
                    # 流式打字阶段：去掉符号直接显示，防止中途渲染崩溃
                    safe_text = full_text.replace("$", "").replace("\\", "")
                    result_placeholder.markdown(safe_text)
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        # 【最终渲染】全部结束后，修复格式并渲染
        final_text = fix_markdown_format(full_text)
        
        result_placeholder.empty()
        result_placeholder.markdown(final_text)
        
        # 👈 如果因为字数限制被截断了，给用户一个提示
        if finish_reason == "length":
            st.warning("⚠️ 注意：这道题非常复杂，模型输出达到了长度上限，后半部分可能被截断。建议分次拍照上传。")
            
        return final_text

    except requests.exceptions.ConnectTimeout:
        status_text.error("连接超时：网络出口受限，请检查网络")
    except requests.exceptions.ReadTimeout:
        status_text.error("读取超时：模型处理时间超过 120 秒")
    except Exception as e:
        status_text.error("请求异常：" + str(e))
    return None


# ================= 页面 UI =================
st.title("全学科错题识别与归因分析") # 👈 顺手把标题也改成全学科了
upload_file = st.file_uploader("上传错题照片", type=["png", "jpg", "jpeg"])

if upload_file is not None:
    st.image(upload_file, caption="上传的错题", width="stretch")
    file_bytes = upload_file.read()

    if st.button("开始分析", type="primary"):
        compressed_bytes = compress_image(file_bytes)
        b64_data = base64.b64encode(compressed_bytes).decode("utf-8")

        status_text = st.empty() 
        status_text.info("正在分析错题，请稍候...")
        
        result_placeholder = st.empty() 
        
        result = analyze_with_stream(b64_data, status_text, result_placeholder)
        
        if result:
            status_text.success("分析完成")
