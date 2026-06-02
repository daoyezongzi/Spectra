# Spectra

Spectra 是一个帮助你整理网易云音乐大歌单的工具。

它的核心思路不是全自动分发，而是：
- 先抓取歌曲相关标签
- 再做规则归类
- 然后由你人工复核
- 最后通过标签墙快速圈歌，写回到新的或已有的网易云歌单

## 适合做什么

- 把一个很大的混合歌单拆成多个小歌单
- 先批量抓标签，再人工修正明显错误
- 用标签筛出某一类歌，再单独建歌单

## 当前流程

1. 扫码登录网易云
2. 拉取你的歌单
3. 选择一个要处理的歌单
4. 抓取原始标签
5. 执行归一化
6. 在“人工复核”里检查和修改结果
7. 在标签墙中筛歌
8. 写入已有歌单，或新建歌单

## 页面上怎么用

### 1. 扫码鉴权

- 点击“生成二维码”
- 用网易云音乐 App 扫码
- 点击“检查扫码状态”

### 2. 歌单读取与增量比对

- 点击“拉取我的歌单”
- 选择要处理的歌单
- 点击“读取当前歌单”

### 3. 原始标签挖掘

- 点击“开始 / 继续抓取”
- 如果中途需要暂停，可以点击“暂停抓取”
- 如果想重来，点击“从头重新抓取”

### 4. 标签归一化清洗

- 点击“执行归一化”
- 这里会先给出归类摘要

### 5. 人工复核

这一步就是检查机器分得对不对。

- 默认先看需要处理的歌曲
- 如果机器分错了，直接修改对应字段
- 如果这首歌你暂时不想让它进入标签墙，就保留“待复核”
- 改完后点击“保存人工复核结果”

### 6. 单歌单标签分发

- 在标签墙里点选标签
- 系统会实时给出命中的歌曲
- 命中的歌曲默认全选
- 你可以在结果表里手动取消个别歌曲
- 最后选择：
  - 加入已有歌单
  - 新建歌单

## 启动方式

### 手动启动

```powershell
cd D:\Github_Storage\Spectra
copy .env.example .env
python -m pip install -r requirements.txt
python -m streamlit run app/main.py
```

### 批处理启动

```powershell
run_spectra.bat
```

这个脚本会：
- 自动检查 `python` 和 `npx.cmd`
- 自动补一个 `.env`
- 启动本地 NeteaseCloudMusicApi
- 再启动 Spectra 页面

## 环境变量

`.env.example`：

```env
SPECTRA_SOURCE_MODE=real
SPECTRA_AUTO_SAVE_LOGIN=true
NETEASE_API_BASE_URL=http://127.0.0.1:3000
NETEASE_COOKIE=
NETEASE_UID=
```

说明：

- `NETEASE_COOKIE` 和 `NETEASE_UID` 可以为空
- 为空时直接走扫码登录
- 如果勾选“登录后自动写回 .env”，最新登录态会被写回本地 `.env`

## 项目结构

```text
Spectra/
  app/
    main.py
    spectra_v1/
  backend/
  frontend/
  docs/
  data/
  requirements.txt
  run_spectra.bat
```

## 当前待办

- 给待复核和脏数据做单独的人审入口
- 清理历史 `processed.json` 中已经写坏的乱码记录
- 后续把词云作为可视化能力补上
