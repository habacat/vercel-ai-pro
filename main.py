import hashlib
import json
import time

import requests
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
from collections import OrderedDict

def md5(msg: str):
    if isinstance(msg, str):
        hash_obj = hashlib.md5()
        hash_obj.update(msg.encode())
        return hash_obj.hexdigest()


class LRUCache:
    def __init__(self, capacity, key_hash_func=md5):
        self.cache = OrderedDict()
        self.capacity = capacity
        self.key_hash_func = key_hash_func

    def __getitem__(self, key):
        if self.key_hash_func:
            key = self.key_hash_func(key)
        if key not in self.cache:
            return None
        else:
            # 将元素移到字典末尾表示最近访问
            self.cache.move_to_end(key)
            return self.cache[key]

    def get(self, key, default=None):
        value = self[key]
        return value if value is not None else default

    def __setitem__(self, key, value):
        if self.key_hash_func:
            key = self.key_hash_func(key)
        if key in self.cache:
            # 更新键值，并将元素移到字典末尾
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            # 弹出字典开头的元素，即最久未访问的元素
            self.cache.popitem(last=False)


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

find_chat_by_question = LRUCache(1000)
find_last_msg_in_chat = LRUCache(1000)
model_infos = {
    'gpt-3.5-turbo': 'https://chatpro.ai-pro.org/api/ask/openAI',
    'gpt-4-1106-preview': 'https://chatpro.ai-pro.org/api/ask/openAI',
    'gpt-4-pro-max': 'https://chatpro.ai-pro.org/api/ask/openAI',

    'chat-bison': 'https://chatpro.ai-pro.org/api/ask/google',
    'text-bison': 'https://chatpro.ai-pro.org/api/ask/google',
    'codechat-bison': 'https://chatpro.ai-pro.org/api/ask/google',

    'openchat_3.5': 'https://chatpro.ai-pro.org/api/ask/Opensource',
    'zephyr-7B-beta': 'https://chatpro.ai-pro.org/api/ask/Opensource',
}
default_model = 'gpt-4-pro-max'

@app.options('/v1/chat/completions')
async def pre_chat():
    return Response()


@app.post('/v1/chat/completions')
async def chat(request: Request):
    # 解析接收到的请求体为POST请求
    request_json = await request.json()
    messages = request_json['messages']
    chat_id = None
    if len(messages) > 2:
        # 上下文
        for msg in messages:
            if msg['role'] == 'user':
                chat_id = find_chat_by_question.get(msg['content'])
                break

    text = request_json['messages'][-1]['content']
    model = request_json['model']

    def next_chat_web(msg):
        return 'data: ' + json.dumps({
            'id': f'chatcmpl-{time.time()}',
            'created': time.time(),
            'object': "chat.completion.chunk",
            'model': model,
            'choices': [{
                'delta': {'content': msg},
                'index': 0,
                'finish_reason': None if msg else 'stop'
            }]
        }, ensure_ascii=False) + '\n\n'

    def next_chat_web_summary(msg):
        return {
            'id': f'chatcmpl-{time.time()}',
            'created': time.time(),
            'object': "chat.completion.chunk",
            'model': model,
            'choices': [{
                'message': {'content': msg},
                'index': 0
            }]
        }

    if request_json.get('stream'):
        # 返回流式响应
        def generate():
            print('-' * 30, '\n')
            print('question: \n', text)
            print('-' * 30, '\n')
            print('answer: ')
            for word in answer_stream(model, chat_id, text):
                print(word, end='')
                yield next_chat_web(word)
            print()
            yield 'data: [DONE]\n\n'

        return StreamingResponse(generate(), media_type="text/event-stream")
    else:
        # 生成摘要
        print('-' * 30, '\n')
        print('question: \n', text)
        print('-' * 30, '\n')
        print('summary: ', end='')
        summary = ''
        for word in answer_stream(model, chat_id, text, True):
            print(word, end='')
            summary = summary + word
        print()
        return next_chat_web_summary(summary)


@app.get("/")
async def read_root():
    return {"Hello": "World"}

def answer_stream(model, chat_id, question='鲁迅为什么暴打周树人？', summary=False):
    if model not in model_infos:
        model = default_model
    url: str = model_infos[model]
    endpoint = url[url.rfind('/') + 1:]
    req_json = {
        "sender": "User",
        "current": True,
        "isCreatedByUser": True,
        "conversationId": chat_id,
        'parentMessageId': find_last_msg_in_chat.get(chat_id, '00000000-0000-0000-0000-000000000000'),
        "error": False,
        "generation": "",
        "responseMessageId": None,
        "overrideParentMessageId": None,
        "text": question,
        "endpoint": endpoint,
        "model": model,
        "chatGptLabel": None,
        "promptPrefix": None,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "frequency_penalty": 0,
        "token": None,
        "isContinued": False,
        "isLimited": False
    }
    headers = {
        "Accept": "*/*",
        "Authorization": "Bearer none", # 在这里设置秘钥
        "Content-Type": "application/json",
        "Origin": "https://chatpro.ai-pro.org",
        "Referer": "https://chatpro.ai-pro.org/chat/new",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    resp = requests.post(url, json=req_json, headers=headers, stream=True)
    resp.encoding = 'utf-8'
    last_text = ''
    lines = resp.iter_lines(decode_unicode=True)
    for data in lines:
        # 首条消息包含对话信息
        if data.startswith('data'):
            infos = json.loads(data[6:])
            chat_id = infos['message']['conversationId']
            find_chat_by_question[question] = chat_id
            if not summary:
                msg_id = infos['message']['messageId']
                find_last_msg_in_chat[chat_id] = msg_id
            break
    for data in lines:
        if data.startswith('data'):
            infos = json.loads(data[6:])
            if 'text' in infos:
                text = infos['text']
                yield text[len(last_text):]
                last_text = text


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
