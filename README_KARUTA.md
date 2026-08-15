# Dr.Memeかるた セットアップ

`/karuta` でDiscordチャンネルに募集を作り、参加者へ専用URLをDMして、ブラウザ上で50枚のミーム画像から早押しするWebゲームです。VOICEVOX ENGINEはBot側から呼び出し、参加者全員が同じ音声ファイルを聞きます。

## 追加ファイル

```text
karuta/
  manager.py
  models.py
  security.py
  voicevox.py
  web.py
cogs/
  karuta_commands.py
web/
  karuta.html
  static/css/karuta.css
  static/js/karuta.js
tests/
  test_karuta_manager.py
```

## .env

`.env.example` をコピーしたあと、少なくとも以下を確認してください。

```env
WEB_HOST=127.0.0.1
WEB_PORT=8080
PUBLIC_BASE_URL=http://127.0.0.1:8080
VOICEVOX_BASE_URL=http://127.0.0.1:50021
VOICEVOX_EXCLUDED_STYLE_IDS=
KARUTA_MIN_REACTION_MS=80
```

LANや外部から参加させる場合は、`PUBLIC_BASE_URL` を参加者のブラウザから到達できるURLにしてください。本番公開ではHTTPS/WSSで公開するリバースプロキシやVPSを使ってください。

## VOICEVOX ENGINE

1. VOICEVOX ENGINEを起動します。
2. 既定では `http://127.0.0.1:50021` に接続します。
3. 特定スタイルを除外したい場合は `VOICEVOX_EXCLUDED_STYLE_IDS=3,8` のようにカンマ区切りで指定します。

## 起動

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

`python bot.py` でDiscord BotとWebサーバーが同時に起動します。Webサーバーは既定で `http://127.0.0.1:8080` です。

## Koyeb

KoyebではWebSocket付きのブラウザゲームを公開するため、WorkerではなくWeb Serviceとして動かしてください。`Procfile` の `web: python bot.py` を使います。

Koyebの環境変数例:

```env
DISCORD_TOKEN=DiscordのBot Token
TEST_GUILD_ID=開発サーバーID
BACKEND=supabase
MEME_EDITOR_ROLE=Dr.Meme
MEME_COOLDOWN_SECONDS=10
ALLOW_EVERYONE_TO_EDIT=false
SYNC_GLOBAL_COMMANDS=true
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=Supabaseのservice_role key
SUPABASE_BUCKET=memes
WEB_HOST=0.0.0.0
PUBLIC_BASE_URL=https://your-app-name.koyeb.app
VOICEVOX_BASE_URL=https://your-voicevox-engine.example.com
VOICEVOX_EXCLUDED_STYLE_IDS=
KARUTA_MIN_REACTION_MS=80
```

`WEB_PORT` は未設定で構いません。Koyebが渡す `PORT` を自動で使います。

注意: `VOICEVOX_BASE_URL=http://127.0.0.1:50021` はKoyeb上では同じコンテナ内にVOICEVOX ENGINEがない限り使えません。Koyebから到達できるVOICEVOX ENGINEを別途用意し、必要ならIP制限や認証付きプロキシで保護してください。

## Discord側

Discord Developer PortalのOAuth2 URLには、既存の `bot` に加えて `applications.commands` scope が必要です。Bot Permissionsは最低限、メッセージ送信、履歴閲覧、ファイル添付、Application Commandsの利用が必要です。

## 最初のテスト

1. Memelistに有効なミーム画像を50件以上登録します。
2. VOICEVOX ENGINEを起動します。
3. `python bot.py` を起動します。
4. Discordで `/karuta` を実行します。
5. 2人以上で参加し、募集者が「ゲームを開始」を押します。
6. 各参加者がDMのURLを開き、自分のDiscordアイコンを押してReadyにします。

## ローカルプレビュー

Discord、Supabase、VOICEVOXなしで画面だけ試す場合:

```powershell
.\.venv\Scripts\Activate.ps1
python karuta_preview.py --port 18080
```

起動すると `data/karuta_preview_urls.txt` に3人分のURLが出ます。まず `Primary` を開くと、疑似ミーム画像50枚と短いダミー音声でUIを確認できます。複数人の見え方を試す場合は `Player B` と `Player C` も別タブや別端末で開いてください。

## 実装済みルール

- 有効なMemelistから50枚を抽選し、1枚を未読札として残します。
- 49戦分の読み順、盤面配置、VOICEVOX話者、0〜4秒待機時間をゲーム開始時に固定します。
- WebSocketでReady、画像ロード、戦開始、札消去、勝者、お手付き、中間確認、終了、解散を同期します。
- 正解クリックはクライアント計測の反応時間を受け取り、短い判定猶予内の最短反応時間で勝者を決めます。
- 誤札は「せっかちニキ」として当該戦と次戦を操作不能にします。
- その戦で獲得可能な全員がお手付きした場合は即終了します。
- 25枚獲得時に中間確認を挟みます。
- 終了時に順位、獲得枚数、平均/最速反応時間、お手付き回数を表示します。
- WebのMemelist画面で読み方を一時変更し、ゲーム終了時にDBへまとめて保存します。

## 制限事項

- 専用URLは通常終了後も同じ部屋で再戦できるよう維持します。解散時は無効扱いになります。
- Supabase Storageの画像欠損確認は開始前に厳密には行わず、local backendではファイル存在を確認します。
- 実Discord接続、DM送信、VOICEVOXの実音声生成は各環境のToken/ENGINE起動が必要です。
