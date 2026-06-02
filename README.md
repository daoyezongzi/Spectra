# Spectra

Spectra 是一个整理网易云大歌单的工具，目标不是全自动替你分发，而是先批量抓标签、做归类，再让你用标签墙快速圈歌并写回歌单。

## 这版能做什么

- 扫码登录网易云并复用本地登录态
- 拉取你的歌单并读取目标歌单曲目
- 批量抓取歌曲证据和原始标签
- 执行标签归一化
- 用标签墙筛歌
- 把结果写入已有歌单，或直接新建歌单
- 只在必要时做少量人工微调

## 当前主流程

1. 扫码登录网易云
2. 拉取歌单列表
3. 读取一个目标歌单
4. 开始或继续抓取原始标签
5. 执行归一化
6. 如果结果明显不对，再打开“人工微调”
7. 在标签墙里筛歌
8. 写回已有歌单，或新建歌单

`人工微调` 在这一版里是可选步骤，不是默认主路径。

## 页面怎么用

### 1. 扫码登录

- 点击“生成二维码”
- 用网易云音乐 App 扫码
- 点击“检查扫码状态”
- 登录成功后，当前会话会立即拿到 `cookie` / `uid`

### 2. 读取歌单

- 点击“拉取我的歌单”
- 选择一个要处理的歌单
- 点击“读取当前歌单”

### 3. 抓取原始标签

- 点击“开始 / 继续抓取”
- 中途可以“暂停抓取”
- 想重来就点“从头重新抓取”
- 如果本地 API 短暂掉线，程序会尝试自动恢复

### 4. 执行归一化

- 点击“执行归一化”
- 页面会给出归类摘要
- 这一步结束后，通常就可以直接去标签墙选歌

### 5. 可选：人工微调

只有当分类结果明显不对时，再展开“打开人工微调表”。

你可以改这些字段：
- 一级类目
- 二级类目
- 语种
- 情绪
- 场景
- 主题

如果某首歌暂时不想参与当前轮选歌，可以勾选“暂不参与选歌”。

### 6. 标签墙筛歌

- 在标签墙里点选标签
- 系统会实时给出命中的歌曲
- 命中结果默认全选
- 你可以在结果表里手动取消个别歌曲
- 选好后可加入已有歌单，或新建歌单

补充：
- 同一个分组内支持多选
- 不同分组会一起生效
- 当原始标签一次选了 3 个及以上时，系统会自动收紧匹配

## 启动方式

### 推荐方式

```powershell
run_spectra.bat
```

这个脚本会自动：
- 检查 `python` 和 `npx.cmd`
- 必要时安装 `requirements.txt`
- 补一个本地 `.env`
- 启动本地 `NeteaseCloudMusicApi`
- 如果 API 意外退出，会自动拉起
- 启动 Spectra 页面

默认端口：
- API：`18631`
- Web：`18701`

### 手动启动

终端 A：

```powershell
cd D:\Github_Storage\Spectra
npx.cmd --yes NeteaseCloudMusicApi
```

终端 B：

```powershell
cd D:\Github_Storage\Spectra
python -m pip install -r requirements.txt
python -m streamlit run app/main.py --server.port 18701
```

如果你已经开了 Web，但 API 没起来，也可以直接在侧边栏点击“启动本地 API”。

## 配置和环境变量

这一版默认配置走 [`config.toml`](./config.toml)，不是让用户手填一堆环境变量。

`config.toml`：

```toml
[spectra]
netease_api_base_url = "http://127.0.0.1:18631"
auto_save_login = true
```

说明：
- `netease_api_base_url` 是业务 API 地址，默认就是本机 `18631`
- `auto_save_login = true` 时，扫码成功后的登录态会自动写回本地 `.env`

`.env` / `.env.example` 现在只保留隐私相关字段：

```env
NETEASE_COOKIE=
NETEASE_UID=
```

说明：
- 普通用户不用手填这两个值
- 为空时直接扫码登录
- 如果开启自动保存，程序会自己把最新的 `cookie` / `uid` 写回 `.env`
- `.env` 是本地私有运行文件，不参与仓库提交

## 项目结构

```text
Spectra/
  app/
    main.py
    spectra_v1/
      config.py
  backend/
  frontend/
  docs/
  data/
  config.toml
  requirements.txt
  run_spectra.bat
```

## 当前待办

- 给脏数据做单独的人审入口
- 清理历史 `processed.json` 中已经写坏的乱码记录
- 把词云可视化补上
