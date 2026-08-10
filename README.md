# RestAcademy 健康データダッシュボード

レストアカデミー（TSUNAGU・小松晃院長）のモニター参加者ヘルスデータを、
zipをアップロードするだけでインタラクティブに分析できる社内ツール。

2026-08-10 松浦冬馬様フォローアップ分析（RHR/HRV/VO2Max/歩数/24時間プロファイル/
Apple Watch装着ギャップ検知）で確立したロジックをそのまま流用している。

**利用者**: 中山さん・小松様（内部限定。参加者本人はアップロードしない運用）
**対応デバイス**: Apple Health（iPhone/Apple Watch）のみ。MiFitness（森田様・石田様が使用）は次フェーズで対応予定。

## セットアップ

```bash
cd /Users/TOMO/dev/restacademy-dashboard
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# secrets.toml の app_password を実際のパスワードに書き換える
streamlit run app.py
```

ブラウザで `http://localhost:8501` が開く。

## 使い方

1. サイドバーからApple Healthのエクスポート（`.zip` または `.xml`）をアップロード
2. gigafile等で二重zip・パスワード付きになっている場合は、パスワード欄に入力（1つで両方の階層に自動トライする）
3. `<Me>` のDOBから `data/participants.yaml` の既存参加者と自動照合。一致しなければ新規参加者の可能性としてアラート表示
4. KPIカード・装着ギャップアラート・タブ別チャート（RHR/HRV/歩数/VO2Max/装着ギャップ/1日の流れ/曜日ヒートマップ/睡眠・ワークアウト）が表示される

## 既知の制約（2026-08-10 MVP時点）

- MiFitness（森田様・石田様のデバイス）は未対応。CSVスキーマが実データで未確認のため、
  パスワード解除済みの実サンプルが手に入ってから `parsers/mifitness.py` を追加する
- PDF出力・Google Driveへの自動アップロードは未実装（今回は「ダッシュボードで見る」を優先スコープとしたため）。
  必要になったら `parsers/apple_health.py` の集計結果から、以前と同じ経路（Chrome headless→PDF→Drive API）で追加できる
- パスワードゲートは共有シークレット方式（簡易）。厳格な個人認証ではない
- ホスティング未決定。ローンチ時は `streamlit run app.py` でローカル起動→ngrok/Tailscale等で
  小松様（広島）にアクセスしてもらう想定。恒常運用する場合はStreamlit Community Cloud等への
  デプロイ＋`secrets.toml`をクラウド側のSecrets管理に移す

## ディレクトリ構成

```
restacademy-dashboard/
├── app.py                    # Streamlitエントリポイント
├── parsers/apple_health.py   # export.xml ストリームパーサー
├── analysis/metrics.py       # RHR/HRV/歩数/装着ギャップ等の集計ロジック
├── analysis/roster.py        # 既存参加者名簿との照合（DOBキー）
├── data/participants.yaml    # 参加者名簿・Drive既存レポートID
└── .streamlit/config.toml    # アップロード上限1GB・カラーテーマ
```
