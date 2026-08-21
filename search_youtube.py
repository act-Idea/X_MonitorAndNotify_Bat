import requests
import json

# =========================
# 設定
# =========================

# YouTube Data APIのAPIキー
API_KEY = 'AIzaSyBZbK3ikog1uhRW54Kt3OX6mdUz056BEEQ'

# 検索キーワード
SEARCH_KEYWORD = 'ちいかわ'

# 取得する動画数
MAX_RESULTS = 5


# ========================================
# YouTube Data APIのエンドポイント
# ========================================

# 動画の検索：search.list
YOUTUBE_SEARCH_ENDPOINT = 'https://www.googleapis.com/youtube/v3/search'

# 動画情報の取得：videos.list
YOUTUBE_VIDEOS_ENDPOINT = 'https://www.googleapis.com/youtube/v3/videos'


# ========================================
# 動画を検索する
# search.list
# ========================================

def search_videos(keyword):

    # ----------------------------------------
    # ① 動画の検索：search.list
    # 「keyword」でYouTube動画を検索する
    #
    # APIから取得できる主な情報：
    #
    # 【動画に関する情報】
    # - 動画ID
    # - 動画タイトル
    # - 動画の説明
    # - 公開日時
    # - サムネイル
    #
    # 【チャンネルに関する情報】
    # - チャンネルID
    # - チャンネル名
    #
    # 【検索結果に関する情報】
    # - 検索結果の種類
    # - 検索結果の件数
    # - 次のページを取得するための情報
    # ----------------------------------------

    search_params = {
        'part': 'snippet',         # 検索結果の基本情報を取得
        'q': keyword,              # 検索キーワード
        'key': API_KEY,            # APIキー
        'type': 'video',           # 動画のみ検索
        'maxResults': MAX_RESULTS  # 最大取得件数
    }

    try:

        # YouTube APIに検索リクエストを送信
        response = requests.get(
            YOUTUBE_SEARCH_ENDPOINT,
            params=search_params
        )

        # HTTPエラーがあれば例外を発生させる
        response.raise_for_status()

        # APIから返ってきたJSONをPythonで扱える形に変換
        search_result = response.json()


        # ----------------------------------------
        # ①で取得した検索結果から動画IDを取り出す
        # ----------------------------------------

        video_ids = []

        for item in search_result['items']:

            video_id = item['id']['videoId']

            video_ids.append(video_id)


        # ----------------------------------------
        # 検索結果がなかった場合
        # ----------------------------------------

        if not video_ids:

            print()
            print("========================================")
            print("          【YouTube動画検索】")
            print("========================================")
            print(f"【検索キーワード】 {keyword}")
            print("【取得件数】 0件")
            print("========================================")
            print()

            return


        # ----------------------------------------
        # 検索条件を表示
        # ----------------------------------------

        print()
        print("========================================")
        print("          【YouTube動画検索】")
        print("========================================")
        print(f"【検索キーワード】 {keyword}")
        print(f"【取得件数】 {len(search_result['items'])}件")
        print("========================================")
        print()


        # ----------------------------------------
        # ② 動画情報の取得：videos.list
        # ①で取得した動画IDを使って、
        # 動画の詳細情報を取得する
        #
        # 動画所有者でなくても取得できる情報：
        #
        # 【基本情報：snippet】
        # - 動画ID
        # - チャンネルID
        # - 動画タイトル
        # - 動画の説明
        # - 公開日時
        # - 更新日時
        # - チャンネル名
        # - サムネイル
        # - タグ
        # - カテゴリID
        #
        # 【コンテンツ情報：contentDetails】
        # - 動画の長さ
        # - 動画の画質
        # - 字幕の有無
        # - 地域制限
        # - ライセンス情報
        # - 年齢制限に関する情報
        #
        # 【統計情報：statistics】
        # - 再生回数
        # - 高評価数
        # - コメント数
        #
        # 【ステータス情報：status】
        # - 公開状態
        # - ライセンス
        # - 埋め込み可能かどうか
        # - 公開範囲
        #
        # 【トピック情報：topicDetails】
        # - 動画に関連するトピック
        #
        # 【ライブ配信情報：liveStreamingDetails】
        # - 配信予定日時
        # - 配信開始日時
        # - 配信終了日時
        # - ライブチャットID
        #
        # 【撮影情報：recordingDetails】
        # - 撮影場所
        # - 撮影日時
        #
        # 【多言語情報：localizations】
        # - 言語ごとのタイトル
        # - 言語ごとの説明
        #
        # ※動画所有者など、特定の権限が必要な情報は除外しています
        # ----------------------------------------

        video_params = {
            'part': 'statistics',       # 再生回数・高評価数・コメント数を取得
            'id': ','.join(video_ids),  # ①で取得した動画ID
            'key': API_KEY              # APIキー
        }

        # YouTube APIに動画情報取得リクエストを送信
        video_response = requests.get(
            YOUTUBE_VIDEOS_ENDPOINT,
            params=video_params
        )

        # HTTPエラーがあれば例外を発生させる
        video_response.raise_for_status()

        # APIから返ってきたJSONをPythonで扱える形に変換
        video_result = video_response.json()


        # ----------------------------------------
        # 動画IDごとに統計情報を整理
        # ----------------------------------------

        statistics_by_id = {}

        for item in video_result['items']:

            video_id = item['id']

            statistics_by_id[video_id] = item.get(
                'statistics',
                {}
            )


        # ----------------------------------------
        # 取得した動画情報を表示
        # ----------------------------------------

        for index, item in enumerate(
            search_result['items'],
            start=1
        ):

            # 動画タイトル
            video_title = item['snippet']['title']

            # 動画ID
            video_id = item['id']['videoId']

            # ②で取得した動画の統計情報
            statistics = statistics_by_id.get(
                video_id,
                {}
            )

            # 再生回数
            view_count = statistics.get(
                'viewCount',
                '取得できませんでした'
            )

            # 高評価数
            like_count = statistics.get(
                'likeCount',
                '取得できませんでした'
            )

            # コメント数
            comment_count = statistics.get(
                'commentCount',
                '取得できませんでした'
            )


            # ----------------------------------------
            # 1件ごとの動画情報を表示
            # ----------------------------------------

            print(f"【{index}件目】")

            print("【動画情報】")

            print(f"【タイトル】 {video_title}")
            print(f"【動画ID】 {video_id}")
            print(
                f"【URL】 https://www.youtube.com/watch?v={video_id}"
            )

            print()

            print("【統計情報】")

            print(f"【再生回数】 {view_count}")
            print(f"【高評価数】 {like_count}")
            print(f"【コメント数】 {comment_count}")

            print("-" * 40)
            print()


    # ========================================
    # エラー処理
    # ========================================

    except requests.exceptions.RequestException as e:

        print(
            f"APIリクエスト中にエラーが発生しました: {e}"
        )

    except KeyError as e:

        print(
            f"APIレスポンスの形式が不正です: {e}"
        )

    except Exception as e:

        print(
            f"予期せぬエラーが発生しました: {e}"
        )


# ========================================
# プログラムを実行
# ========================================

if __name__ == '__main__':

    search_videos(SEARCH_KEYWORD)