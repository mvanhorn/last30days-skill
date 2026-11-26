# /last30days

[English](README.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Español](README.es.md) | [Português (Brasil)](README.pt-BR.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [العربية](README.ar.md) | [فارسی](README.fa.md) | [Türkçe](README.tr.md) | Tiếng Việt

<p align="center">
  <img src="media/pr-assets/last30days-ad.gif" width="720" alt="last30days - an AI agent-led search engine that searches people, not editors" />
</p>

<p align="center">
  <a href="https://github.com/mvanhorn/last30days-skill">
    <img src="https://img.shields.io/badge/%231-Repository%20Of%20The%20Day-6f42c1?style=for-the-badge&logo=github&label=GITHUB%20TRENDING" alt="GitHub Trending #1 Repository Of The Day" />
  </a>
  <br/>
  <a href="https://trendshift.io/repositories/21997" target="_blank">
    <img src="https://trendshift.io/api/badge/repositories/21997" alt="mvanhorn/last30days-skill | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
  </a>
  <br/>
  <a href="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml">
    <img src="https://github.com/mvanhorn/last30days-skill/actions/workflows/validate.yml/badge.svg" alt="Validate status" />
  </a>
</p>

**Một công cụ tìm kiếm do AI agent dẫn dắt, được chấm điểm bằng lượt upvote, lượt thích và tiền thật - không phải bởi biên tập viên.**

README này theo dõi pipeline v4 hiện tại. Đặc tả skill lúc chạy nằm ở [skills/last30days/SKILL.md](skills/last30days/SKILL.md), đó là nguồn sự thật cho hành vi lệnh và thiết lập mới nhất.

**Claude Code (khuyên dùng — tự động cập nhật qua marketplace):**
```
/plugin marketplace add mvanhorn/last30days-skill
/plugin install last30days
```

**Codex, Cursor, Copilot, Gemini CLI, hoặc bất kỳ host nào trong hơn 50 host [Agent Skills](https://agentskills.io):**
```
npx skills add mvanhorn/last30days-skill -g
```
(`-g` cài đặt toàn cục cho người dùng của bạn, dùng được ở mọi dự án. Bỏ nó đi để giới hạn theo từng dự án.)

Thêm lựa chọn cài đặt (claude.ai bản web, OpenClaw, thủ công) ở mục [Cài đặt](#cài-đặt) bên dưới.

Không cần cấu hình. Reddit, HN, Polymarket và GitHub hoạt động ngay lập tức, hồ sơ bằng sáng chế và trang tuyển dụng cũng vậy khi chủ đề của bạn là một công ty. Chạy nó một lần và trình hướng dẫn thiết lập sẽ mở khóa X, YouTube, TikTok, arXiv, Techmeme và hơn nữa trong 30 giây.

---

Lượt upvote trên Reddit. Lượt thích trên X. Bản ghi chép YouTube. Mức tương tác TikTok. Tỷ lệ cược Polymarket được chống lưng bằng tiền thật và thông tin nội bộ. Đó là hàng triệu người bỏ phiếu bằng sự chú ý và ví tiền của họ mỗi ngày. /last30days tìm kiếm tất cả song song, chấm điểm theo những gì người thật thực sự tương tác, và một AI agent đóng vai trò giám khảo tổng hợp thành một bản tóm tắt duy nhất.

Google tổng hợp từ biên tập viên. /last30days tìm kiếm con người.

Bạn không thể có kiểu tìm kiếm này ở bất cứ nơi nào khác vì không một AI nào truy cập được tất cả. Google search không chạm tới bình luận Reddit hay bài đăng X. ChatGPT có thỏa thuận với Reddit nhưng không tìm được X hay TikTok. Gemini có YouTube nhưng không có Reddit. Claude bẩm sinh không có cái nào trong số đó. Mỗi nền tảng là một khu vườn có tường bao quanh với API, token và cơ chế xác thực riêng. Nhưng bạn có thể mang theo khóa và phiên trình duyệt của chính mình, và đột nhiên một AI agent có thể tìm kiếm tất cả chúng cùng lúc, chấm điểm chúng với nhau, và cho bạn biết điều gì thực sự quan trọng.

Đó chính là điểm mở khóa. Không phải một công cụ tìm kiếm tốt hơn. Mà là hàng chục nền tảng rời rạc, được một agent bắc cầu nối lại.

```
/last30days Peter Steinberger
```

Ngày mai bạn có một cuộc họp. Bạn Google về họ. Bạn nhận được LinkedIn của họ từ năm 2023. /last30days cho bạn biết họ thực sự đang làm gì trong tháng này: gia nhập OpenAI để làm Codex, đấu tranh với lệnh cấm agent bên thứ ba của Anthropic, ship 23 PR với tỷ lệ merge 85%, xây dựng "LobsterOS" cho điều khiển agent đa thiết bị, và r/ClaudeCode đạt 569 upvote tranh luận xem anh ấy là anh hùng hay "khó ưa." Rải rác khắp các bài đăng X, chủ đề Reddit, bản ghi chép YouTube và commit GitHub. Không điều nào trong số đó có trên Google.

## Vì sao công cụ này tồn tại

Tôi xây dựng nó để theo kịp AI. Mọi thứ thay đổi mỗi ngày và dân mê Reddit với X luôn nắm bắt trước tiên. Tôi cần prompt tốt hơn, và dữ liệu huấn luyện luôn chậm hơn vài tháng so với những gì cộng đồng đã tìm ra.

Nhưng nó đã trở thành thứ lớn hơn. Giờ tôi chạy nó trước một cuộc gọi bán hàng để biết sự thật 30 ngày qua về một doanh nghiệp. Trước một cuộc họp để đọc tweet gần đây và bản ghi chép podcast của ai đó. Trước chuyến đi Disney World để biết trò chơi nào đang đóng cửa và cộng đồng nói gì về Genie+. Trước khi xây dựng bất cứ thứ gì để biết mọi người thực sự đang gặp vấn đề gì.

Nếu bạn sắp gặp một CEO, bạn đã đọc hết tweet và bản ghi chép YouTube của họ trong 30 ngày qua chưa? Tôi thì rồi.

## Các nguồn tin, được chấm điểm bởi chính mọi người

| Nguồn | Mọi người cho bạn biết điều gì |
|--------|--------------------------|
| **Reddit** | Góc nhìn không lọc. Các bình luận hàng đầu với số upvote thật, miễn phí, không cần API key. Những ý kiến thật mà Google chôn vùi. Khi bạn đồng ý, nó còn có thể tìm kiếm dưới danh nghĩa tài khoản Reddit đã đăng nhập của chính bạn để có thêm chủ đề và điểm trực tiếp. |
| **X / Twitter** | Quan điểm nóng, chủ đề chuyên gia, phản ứng nóng hổi. Biết đầu tiên, tranh luận đầu tiên. |
| **YouTube** | Bài phân tích sâu 45 phút. Toàn bộ bản ghi chép được tìm kiếm để lấy 5 câu đáng trích dẫn quan trọng nhất. |
| **TikTok** | Nhà sáng tạo tiếp cận 3.6M người với một quan điểm bạn sẽ không bao giờ tìm thấy trên Google. |
| **Instagram Reels** | Góc nhìn của influencer kèm bản ghi chép lời nói. Tín hiệu văn hóa thị giác. |
| **Hacker News** | Sự đồng thuận của giới lập trình viên. 825 điểm, 899 bình luận. Nơi dân kỹ thuật thực sự tranh luận. |
| **Polymarket** | Không phải ý kiến. Là tỷ lệ cược. Được chống lưng bằng tiền thật. 96% tin tưởng vào doanh số album. 4% vào một thương vụ mua lại. Báo cáo đặt tỷ lệ cược của một thị trường trên một dòng dưới câu chuyện nó liên quan, kèm mức biến động của chúng. |
| **GitHub** | Với con người: tốc độ PR, các repo hàng đầu theo sao, ghi chú phát hành. Với chủ đề: issue và thảo luận. |
| **Digg** | Các cụm câu chuyện từ mọi chủ đề của Digg (AI, công nghệ, thế giới và kinh doanh, thể thao, khoa học, game, giải trí), xếp hạng theo Digg Score, với trích dẫn inline ghi rõ nguồn từ X (không cần xác thực X). Tự động bật khi `digg-pp-cli` có trên PATH. `digg-pp-cli` cũ hơn vẫn hoạt động, chỉ trên AI và công nghệ và không có điểm. |
| **arXiv** | Các bài báo đằng sau cơn sốt. Nghiên cứu mới trong khoảng thời gian, miễn phí, không cần API key. Tự động bật khi `arxiv-pp-cli` có trên PATH (thiết lập lần đầu sẽ cài nó). |
| **Techmeme** | Lớp biên tập tin công nghệ, giới hạn theo cửa sổ 30 ngày của bạn. Miễn phí, không cần API key. Tự động bật khi `techmeme-pp-cli` có trên PATH (thiết lập lần đầu sẽ cài nó). |
| **Yahoo Finance** | Cách thị trường nhìn một công ty. Biến động giá cổ phiếu trong 30 ngày của bạn, đặt cạnh S&P 500 đối với các mã niêm yết tại Mỹ. Miễn phí, không cần API key. Hiển thị khi chủ đề của bạn là một công ty niêm yết hoặc người lãnh đạo công ty đó và `yahoo-finance-pp-cli` có trên PATH (thiết lập lần đầu sẽ cài nó). |
| **LinkedIn** | Tín hiệu chuyên nghiệp. Bài đăng và bài viết, với bài viết được trọng số là tín hiệu cao. |
| **Meta Ads** | Một thương hiệu đang trả tiền để nói gì. Các mẫu quảng cáo Meta trực tiếp ra mắt trong tháng này, kèm nội dung, mã khuyến mãi và bản ghi chép lời nói. |
| **StockTwits** | Tâm lý nhà giao dịch. Tự động kích hoạt khi chủ đề của bạn là một mã cổ phiếu hoặc crypto. |
| **Threads** | Lớp văn bản thời hậu Twitter. Các cuộc trò chuyện từ nhà sáng tạo và thương hiệu. |
| **Pinterest** | Khám phá trực quan. Pin, lượt lưu và bình luận về sản phẩm và ý tưởng. |
| **Xiaohongshu (RED)** | Tín hiệu lối sống, sản phẩm và nhà sáng tạo của Trung Quốc. Yêu cầu rõ ràng bằng `--search xhs` khi một plugin trình duyệt x-mcp đã đăng nhập hoặc dịch vụ `xiaohongshu-mcp` đang chạy cục bộ. |
| **Bluesky** | Lớp mạng xã hội phi tập trung. Các bài đăng AT Protocol từ làn sóng rời Twitter. |
| **Perplexity** | Tổng hợp Agent API có kiểm soát, dự phòng OpenRouter Sonar, các hàng Search API thô, và Deep Research tường minh. |
| **DEV Community (dev.to)** | Bài viết của lập trình viên và bình luận hàng đầu qua API công khai, không cần khóa. Chọn tham gia bằng `--devto-tags`, `--search devto`, hoặc `INCLUDE_SOURCES=devto`. |
| **Truth Social** | Bài đăng được tìm kiếm bằng `TRUTHSOCIAL_TOKEN`; khả dụng khi đã xác thực. |
| **Telegram** | Bài đăng công khai từ các kênh được nêu tên. Tuỳ chọn tham gia; yêu cầu `SCRAPECREATORS_API_KEY` và một danh sách kênh. Không khám phá theo từ khóa. |
| **Trustpilot** | TrustScore của công ty, số lượng đánh giá, và bản tóm tắt phản hồi khách hàng bằng AI. Tuỳ chọn tham gia; yêu cầu `trustpilot-pp-cli` trên PATH và một phiên khả dụng. |
| **Amazon** | Đánh giá sản phẩm, giá và nhận xét của khách hàng. Tuỳ chọn tham gia; yêu cầu CLI `brightdata` đã xác thực và dùng tín dụng Bright Data của bạn. |
| **DripStack** | Bản tin tài chính và bài phân tích của chuyên gia qua một API công khai, không cần khóa. Chọn tham gia bằng `--search dripstack` hoặc `INCLUDE_SOURCES=dripstack`. |
| **Jobs / careers pages** | Một công ty đang tuyển gì, đọc từ chính trang tuyển dụng của nó (Workday, Apple, Amazon, Google, Microsoft, Netflix, Oracle và hơn nữa). Một lượt chạy về công ty sẽ tự động đọc nó, và các vị trí gợi ý điều gì đó chưa công bố sẽ trở thành Jobs Page Secret Signals. Cũng được chọn bằng `--hiring-signals`, `--search jobs`, hoặc kế hoạch nghiên cứu. |
| **Patents** | Các hồ sơ bằng sáng chế mới tại Mỹ của một công ty, làm bằng chứng về những gì nó đang xây dựng trước khi ra mắt. Miễn phí, không cần API key. Hiển thị khi chủ đề của bạn là một công ty. Một hồ sơ chỉ trở thành Patent Secret Signal khi nó khác thường đối với công ty đó hoặc mô tả thứ gì đó nó chưa ship hay chưa công bố. |
| **Local corpus** | Các file Markdown và văn bản cục bộ của bạn, cộng thêm PDF khi `pdftotext` có trên PATH. Chọn tham gia bằng `--corpus` hoặc `LAST30DAYS_CORPUS_DIRS`. |
| **Web** | Lớp phủ biên tập, các bài so sánh trên blog. Một tín hiệu trong nhiều tín hiệu, không phải tín hiệu duy nhất. |

Hãy yêu cầu `/last30days` bao gồm các nguồn bạn cần. Tính khả dụng phụ thuộc vào thông tin xác thực, công cụ đã cài, chủ đề và các lựa chọn tham gia tường minh. Xem [source requirements](CONFIGURATION.md#source-requirements) để biết cấu hình.

Một chủ đề Reddit với 1.500 upvote là tín hiệu mạnh hơn một bài blog không ai đọc. Một TikTok với 3.6M lượt xem cho bạn biết nhiều hơn về điều gì đang liên quan về văn hóa so với một thông cáo báo chí. Tỷ lệ cược Polymarket được chống lưng bằng khối lượng $66K thì khó cãi hơn phỏng đoán của một chuyên gia bình luận.

Bản tổng hợp xếp hạng theo những gì người thật thực sự tương tác. Độ liên quan xã hội, không phải độ liên quan SEO.

## Mọi người thực sự dùng nó vào việc gì

**Trước một cuộc họp.** `/last30days Peter Steinberger` - gia nhập đội Codex của OpenAI, đấu tranh với lệnh cấm agent bên thứ ba của Anthropic, 23 PR được merge với tỷ lệ 85% trên GitHub, xây dựng LobsterOS cho điều khiển agent đa thiết bị. r/ClaudeCode: "Ever since OpenClaw released, it was widely known that if you run it through anything other than the API, you were gonna get banned eventually" (227 upvote). Điều đó không có trên LinkedIn.

**Để xem một công ty đang xây dựng gì trước khi nó ra mắt.** `/last30days Apple` - Jobs Page Secret Signal gắn cờ một vị trí giám đốc mới kiểm tra xem các yêu cầu có kích hoạt giao dịch chạy qua API đối tác và hệ thống tài chính hay không, nghe như kiểm soát chất lượng cho một trợ lý hoàn tất mua hàng. Patent Secret Signal gắn cờ một đơn đăng ký thay đổi trạng thái của thiết bị bằng cách chạm vào nó trong ảnh đã lưu của căn phòng bạn. Polymarket đặt Apple ở mức 91% trở thành công ty lớn nhất thế giới vào cuối năm. `/last30days Alphabet` tìm thấy một vị trí quản lý sản phẩm cho "YouTube Ads Agentic Buyflows", và dòng cổ phiếu của nó ghi giảm 17.6% trong 30 ngày trong khi S&P 500 giảm 3.6%, với ngày tệ nhất vào 29 tháng 10 (-12.6%). Với báo cáo chỉ về tuyển dụng, thêm `--hiring-signals`.

**Để tìm chủ đề trước khi nó đạt đỉnh.** Hãy hỏi `/last30days what's exploding in AI agents?` và skill chuyển sang chế độ khám phá: engine quét danh sách danh mục Reddit, các câu chuyện front/best của Hacker News, các câu chuyện Digg, và X khi đã xác thực; agent của bạn phán xét các đề cử (tên gọi, lọc rác, mức xứng đáng về nội dung) và viết các góc độ podcast / bài viết X; sau đó bạn nhận được 5-10 chủ đề xếp hạng theo tốc độ. Mọi kết quả đều gồm số liệu đa nguồn, một nhãn động lượng, và một lệnh `/last30days "<topic>"` sẵn sàng chạy để theo tiếp.

**Khi có thứ gì đó vừa ra mắt.** `/last30days Kanye West` - Anh bị Vương quốc Anh chặn visa, Wireless Festival bị hủy, các nhà tài trợ tháo chạy. Nhưng BULLY ra mắt ở vị trí #2 trên Billboard. Fantano quay lại sau "Yay sabbatical" của mình để đánh giá nó (653K lượt xem). SoFi Homecoming mang Lauryn Hill và Travis Scott đến với 44 bài hát. Polymarket: "Will Kanye tweet again?" 86% Có. 23 chủ đề Reddit, 17 video YouTube, 86K upvote.

**Để so sánh công cụ.** `/last30days OpenClaw vs Hermes vs Paperclip` - "These aren't competitors, they're layers." OpenClaw là bên thực thi (351K sao GitHub, trực tiếp), Hermes là bộ não tự cải thiện (31K sao), Paperclip là sơ đồ tổ chức (49K sao). Số sao lấy trực tiếp từ GitHub API, không phải bài blog cũ. Bảng so sánh song song với kiến trúc, bộ nhớ, bảo mật, phù hợp nhất cho. Theo @IMJustinBrooke: "OpenClaw = Charmander, Hermes = Charizard."

**Để hiểu thế giới.** `/last30days Iran vs USA` - Ngày 38 của cuộc chiến. Hạn chót thứ Ba của Trump cho Iran mở lại eo biển Hormuz. Hai máy bay chiến đấu của Mỹ bị bắn hạ. Dầu ở mức $126/thùng. IEA gọi đây là "the largest supply disruption in the history of the global oil market." Polymarket: ngừng bắn trước 31 tháng 12 ở mức 74%. 27 bài đăng X, 10 video YouTube, 20 thị trường dự đoán.

**Trước một chuyến đi.** `/last30days Universal Epic Universe` - Khu mở rộng đã đang xây dựng. Giấy phép "Project 680" đã nộp. Show pháo hoa được xác nhận bởi hạ tầng nhưng chưa công bố. Thời gian chờ: Mine-Cart Madness trung bình 148 phút. Chưa có vé năm, và dân địa phương đang bức xúc. Stardust Racers ngừng để tân trang đến hết ngày 5 tháng 4.

**Để học nhanh một thứ gì đó.** `/last30days Nano Banana Pro prompting` - Prompt có cấu trúc JSON đang thay thế mớ thẻ lộn xộn. Định dạng lồng nhau của @pictsbyai ngăn "concept bleeding." Quy trình sửa-trước thắng việc tạo lại. Sau đó nó viết cho bạn một prompt hoàn chỉnh dùng đúng những gì cộng đồng nói là hiệu quả.

## Có gì mới

v4 đọc được những gì một công ty đang làm trước khi nó công bố, và khiến mọi báo cáo dễ trích dẫn hơn:

- Patent Secret Signals: một lượt chạy về công ty đọc các hồ sơ bằng sáng chế tại Mỹ của tháng đó, không cần khóa, và chỉ gắn cờ những hồ sơ khác thường hoặc mô tả thứ gì đó chưa công bố.
- Jobs Page Secret Signals: nó đọc trang tuyển dụng của chính công ty và gắn cờ các vị trí làm lộ điều sắp tới, bao gồm các đội mới chưa ai đưa tin.
- Quote lines: các bình luận hay nhất trên Reddit, X, YouTube, TikTok và Instagram nằm trên dòng riêng dưới câu chuyện chúng chống lưng, được dịch khi cần, và một câu nói trong video cũng có thể được trích dẫn.
- Polymarket odds line: một dòng dưới câu chuyện nó liên quan, kèm mức biến động của tỷ lệ cược.
- Yahoo Finance line: giá cổ phiếu của một công ty niêm yết trong 30 ngày qua đặt cạnh S&P 500, cộng với biến động trong một ngày lớn nhất của nó.
- Biết bạn đang nói đến ai: "Apple" tự nó phân giải thành công ty, trang web của nó và mã cổ phiếu của nó.
- Reddit dưới danh nghĩa bạn: khi bạn đồng ý, Reddit tìm kiếm bằng tài khoản đã đăng nhập của chính bạn.
- Digg giờ bao phủ mọi chủ đề, không chỉ AI và công nghệ.

Xem [latest release](https://github.com/mvanhorn/last30days-skill/releases/latest) để biết phiên bản đã phát hành hiện tại và [CHANGELOG.md](CHANGELOG.md) cho các thay đổi theo từng phiên bản. Các thay đổi chưa phát hành trên nhánh mặc định được ghi trong [changelog.d/](changelog.d/).

README này mô tả cây nguồn hiện tại; phiên bản bạn đã cài có thể khác.

## Cài đặt

| Nền tảng | Cài đặt | Cập nhật |
|---------|---------|---------|
| **Claude Code** (khuyên dùng) | `/plugin marketplace add mvanhorn/last30days-skill` | Tự động qua marketplace, hoặc `claude plugin update last30days@last30days-skill` |
| **Grok** (xAI Build CLI) | `grok plugin marketplace add mvanhorn/last30days-skill` rồi `grok plugin install last30days` | `grok plugin update last30days` |
| **Grok Bot** | [Thêm nó từ marketplace của Grok Bot](https://x.ai/bot/marketplace/bots/last30days) | Do marketplace quản lý |
| **Codex, Cursor, Copilot, Gemini CLI, hoặc bất kỳ host nào trong hơn 50 host [Agent Skills](https://agentskills.io)** | `npx skills add mvanhorn/last30days-skill -g` | `npx skills update last30days -g` |
| **claude.ai** (web) | [Tải `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) và tải lên qua claude.ai > Customize > Skills > + > Create skill > Upload a skill | Tải lại và tải lên lại |
| **Claude Desktop** | [Tải `.mcpb` cho nền tảng của bạn](https://github.com/mvanhorn/last30days-skill/releases/latest) và kéo vào Settings > Extensions | Tải lại và kéo gói mới vào |
| **OpenClaw** | `clawhub install last30days-official` | `clawhub update last30days-official` |

### Claude Code (khuyên dùng)

```
/plugin marketplace add mvanhorn/last30days-skill
```

Khuyên dùng vì marketplace của Claude Code xử lý cập nhật cho bạn — bộ nhớ đệm plugin được đánh phiên bản và tự làm mới khi một bản phát hành mới ra mắt. Chạy `claude plugin update last30days@last30days-skill` để ép kiểm tra.

Nếu bạn muốn dùng đường cài đặt agent-skills trên Claude Code, cách đó cũng được hỗ trợ:

```
npx skills add mvanhorn/last30days-skill -g -a claude-code
```

### Link dùng thử nhanh

[Dùng thử Last30Days Research Skill trong Telegram hoặc WhatsApp](https://app.clawmama.run/skills/2ne05f/hermes?utm_source=github&utm_medium=issue&utm_campaign=skill_outreach_mvanhorn_last30days_skill)

Plugin gốc và bản cài `npx skills` có thể cùng tồn tại. Lưu ý Claude Code không khử trùng lặp giữa các phương thức cài đặt: nếu bạn bật cả plugin marketplace và bản sao `npx skills`, `/last30days` sẽ hiển thị hai mục. Hãy dùng một phương thức cài đặt cho mỗi máy.

### Grok (xAI Build CLI)

[Grok Build](https://docs.x.ai/build/features/skills-plugins-marketplaces) (`grok`) cài đặt last30days như một plugin gốc. Cài đặt trực tiếp theo dõi repository:

```bash
grok plugin install mvanhorn/last30days-skill
```

Hoặc thêm repo này làm nguồn marketplace, rồi cài đặt theo tên plugin:

```bash
grok plugin marketplace add mvanhorn/last30days-skill
grok plugin install last30days
```

Thêm `--trust` để bỏ qua xác nhận cài đặt. Cập nhật bằng `grok plugin update last30days`. Grok cũng đọc các manifest của Claude Code để tương thích; cặp `.grok-plugin/` gốc là làn hạng nhất (và là thứ mà một danh sách [xAI marketplace](https://github.com/xai-org/plugin-marketplace) chính thức trỏ tới). `npx skills add` vẫn là dự phòng đa host hợp lệ. Trên [Grok Bot](https://x.ai/bot/marketplace/bots/last30days), nơi last30days cài đặt từ marketplace, X hoạt động ngay từ đầu qua các công cụ X tích hợp của bot, rồi đến trình kết nối X của bot, với X API chính thức (`X_BEARER_TOKEN`) làm dự phòng.

### Codex, Cursor, Copilot, Gemini CLI, và các host Agent Skills khác

Cài đặt qua CLI [Agent Skills](https://agentskills.io) mở — hỗ trợ hơn 50 harness bao gồm `codex`, `cursor`, `github-copilot`, `gemini-cli`, `claude-code`, `windsurf`, `cline`, `continue`, `roo`, `aider-desk`, `opencode`, `goose`, và hơn nữa (danh sách đầy đủ trên [vercel-labs/skills repo](https://github.com/vercel-labs/skills)).

```bash
npx skills add mvanhorn/last30days-skill -g
```

Cờ `-g` (toàn cục) cài đặt vào thư mục người dùng của bạn để skill khả dụng ở mọi dự án. Không có `-g`, `npx skills` cài đặt cục bộ theo dự án vào `./.skills/` (được commit cùng repo). Với một công cụ nghiên cứu thế giới, toàn cục là thứ bạn muốn.

Codex desktop và các host chế độ thư mục khác có thể hoạt động trong thư mục thông thường cũng như repo Git. Để kiểm tra một lượt chạy sẽ đọc và ghi gì mà không bắt đầu nghiên cứu, chạy `scripts/last30days.py --preflight` đi kèm từ thư mục skill đã tải; trong một bản checkout nguồn, lệnh tương đương là `python3 skills/last30days/scripts/last30days.py --preflight`. Nó cho thấy nguồn cấu hình, kế hoạch cookie trình duyệt, các lần ghi dự kiến, lệnh tuỳ chọn, và cấu hình dự án bị bỏ qua mà không đọc cookie, ghi file, hay chạy nghiên cứu. Thiết lập lần đầu không yêu cầu nó.

Theo mặc định, việc này cài đặt cho bất kỳ harness nào `npx skills` phát hiện. Để nhắm một harness cụ thể (hoặc nhiều):

```bash
npx skills add mvanhorn/last30days-skill -g -a codex
npx skills add mvanhorn/last30days-skill -g -a cursor
npx skills add mvanhorn/last30days-skill -g -a gemini-cli
npx skills add mvanhorn/last30days-skill -g -a codex -a cursor
```

Cập nhật sau bằng:

```bash
npx skills update last30days -g
```

Hoặc cập nhật mọi thứ bạn đã cài toàn cục qua `npx skills`:

```bash
npx skills update -g
```

Liệt kê và gỡ bỏ bằng `npx skills list -g` và `npx skills remove last30days -g`.

### DeepSeek Harness

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (DSH) tải các skill từ `~/.agents/skills` (hoặc `$DSH_AGENTS_HOME/skills` khi được thiết lập). Đây chính là thư mục mà lệnh cài đặt toàn cục `npx skills add mvanhorn/last30days-skill -g` ở trên lưu last30days, nên DSH tự nhận diện mà không cần cấu hình thêm. Khởi động lại DSH sau khi cài đặt.

### claude.ai (web)

1. [Tải `last30days.skill`](https://github.com/mvanhorn/last30days-skill/releases/latest/download/last30days.skill) từ bản phát hành mới nhất
2. Vào [claude.ai > Customize > Skills](https://claude.ai/customize/skills)
3. Nhấn nút `+` trong bảng Skills > nhấn `Create skill` > `Upload a skill` và duyệt/thả file vào

Trước tiên hãy bật "Code execution and file creation" trong Capabilities — skill sẽ không chạy nếu thiếu nó.

### Claude Desktop

Claude Desktop cài đặt `/last30days` như một MCP server qua gói `.mcpb` (một gói Model Context Protocol một nhấn).

1. Vào [bản phát hành mới nhất](https://github.com/mvanhorn/last30days-skill/releases/latest) và tải `.mcpb` cho nền tảng của bạn:
   - macOS Apple Silicon: `last30days-pp-mcp-darwin-arm64.mcpb`
   - macOS Intel: `last30days-pp-mcp-darwin-amd64.mcpb`
   - Linux x86_64: `last30days-pp-mcp-linux-amd64.mcpb`
2. Mở Claude Desktop, vào Settings > Extensions, và kéo file vào.
3. Khi được nhắc, dán API key cho các nguồn bạn muốn bật. Mọi trường đều tuỳ chọn — engine giảm xuống chế độ chỉ-web nếu bạn bỏ qua tất cả. Khóa được lưu trong keychain của hệ điều hành của bạn.
4. Khởi động lại Claude Desktop. Hãy yêu cầu Claude "research Peter Steinberger" hoặc bất kỳ chủ đề nào và nó sẽ gọi công cụ `research`.

**Yêu cầu host:** Python 3.12+ trên PATH. Gói này đi kèm nguồn engine nhưng dùng trình thông dịch Python cục bộ của bạn. Cài đặt từ [python.org](https://www.python.org/downloads/) trên Windows; macOS và hầu hết bản phân phối Linux đều đi kèm phiên bản tương thích.

**Khóa không đồng bộ với skill Code.** Claude Desktop và Claude Code duy trì kho thông tin xác thực riêng biệt theo thiết kế. Nếu bạn đã cấu hình `~/.config/last30days/.env` cho skill Code, bạn sẽ nhập lại cùng các khóa đó ở đây một lần.

Hỗ trợ Windows bị hoãn cho đến khi các điểm vào manifest theo nền tảng được sắp xếp xong; theo dõi trong một issue tiếp nối.

### OpenClaw

```bash
clawhub install last30days-official
```

Với các workflow hành động trên X/Twitter nằm ngoài nghiên cứu `/last30days`, chẳng hạn đăng
tweet hoặc trả lời, xuất danh sách người theo dõi, xử lý media, theo dõi, và bốc thăm
giveaway, hãy dùng [TweetClaw](https://github.com/Xquik-dev/tweetclaw) làm plugin đồng hành
cho OpenClaw. TweetClaw do Xquik-dev duy trì và chỉ được liệt kê ở đây như một
đường đồng hành tuỳ chọn, không phải là phụ thuộc hay sự bảo chứng của last30days.

### Thủ công (nhà phát triển)

```bash
git clone https://github.com/mvanhorn/last30days-skill.git
ln -s "$(pwd)/last30days-skill/skills/last30days" ~/.claude/skills/last30days
```

Liên kết tượng trưng giữ bản cài đặt đồng bộ với cây làm việc của bạn khi bạn chỉnh sửa — không cần sao chép lại. Với `claude.ai`, dựng file `.skill` từ nguồn: `bash skills/last30days/scripts/build-skill.sh` tạo ra `dist/last30days.skill`.

Reddit (kèm bình luận), Hacker News, Polymarket và GitHub hoạt động ngay lập tức, cộng thêm hồ sơ bằng sáng chế và trang tuyển dụng cho một chủ đề công ty. Không cần cấu hình. Chạy `/last30days` một lần và trình hướng dẫn thiết lập mở khóa thêm nguồn trong 30 giây, bao gồm các CLI arXiv và Techmeme miễn phí.

## Mang theo khóa của riêng bạn

Các nền tảng này không có quan hệ với nhau. X không biết Reddit nghĩ gì. YouTube không thấy TikTok. Nhưng bạn có thể mang theo API key và token trình duyệt của chính mình, và đột nhiên bạn truy cập được tất cả chúng cùng lúc.

| Nguồn | Bạn cần gì | Chi phí |
|---------|---------------|------|
| Reddit (kèm bình luận) + HN + Polymarket + GitHub + StockTwits + hồ sơ bằng sáng chế và trang tuyển dụng của công ty | Không gì cả | Miễn phí |
| arXiv + Techmeme + Yahoo Finance | CLI miễn phí, được thiết lập lần đầu tự động cài đặt | Miễn phí |
| X / Twitter | Đặt `X_BEARER_TOKEN` cho X API chính thức (bài đăng gần đây, khoảng tuần qua, trừ khi dự án nhà phát triển X của bạn có quyền truy cập kho lưu trữ đầy đủ; một dự phòng trên Grok Bot sau các công cụ X tích hợp của bot, tuỳ chọn tham gia ở nơi khác bằng `LAST30DAYS_X_BACKEND=xapi`), hoặc đăng nhập vào x.com trong bất kỳ trình duyệt nào, hoặc đặt `XQUIK_API_KEY` / `XAI_API_KEY` | Tín dụng X API đến từ dự án nhà phát triển X của bạn; cookie trình duyệt miễn phí; các khóa khác tùy thuộc nhà cung cấp |
| YouTube | `brew install yt-dlp` | Miễn phí |
| Bluesky | Mật khẩu ứng dụng từ bsky.app | Miễn phí |
| TikTok + Instagram + Threads + Pinterest + LinkedIn + bình luận YouTube | Khóa ScrapeCreators | 10.000 cuộc gọi miễn phí, sau đó PAYG |
| Xiaohongshu (RED) | Chạy một plugin trình duyệt x-mcp đã đăng nhập hoặc dịch vụ `xiaohongshu-mcp` và chọn tham gia bằng `--search xhs` mỗi lượt chạy hoặc `INCLUDE_SOURCES=xiaohongshu` trong `.env`; last30days tự thăm dò `http://localhost:18060` rồi `http://host.docker.internal:18060`, hoặc dùng `XIAOHONGSHU_API_BASE` cho một URL tùy chỉnh | Không cần API key của last30days; phụ thuộc vào dịch vụ phiên trình duyệt cục bộ của bạn |
| DripStack (bản tin tài chính cao cấp) | Tuỳ chọn tham gia: `--search dripstack` mỗi lượt chạy, hoặc `INCLUDE_SOURCES=dripstack` trong `.env` | Không cần khóa; API tìm kiếm công khai miễn phí |
| Perplexity Agent API / Search API / Deep Research | Khóa Perplexity, hoặc khóa OpenRouter làm dự phòng Sonar | Trả theo mức dùng; một khóa trực tiếp bật Agent API và Deep Research nền |
| Tìm kiếm web | Khóa Brave Search | 2.000 truy vấn miễn phí/tháng |

### macOS Keychain (tuỳ chọn)

Trên macOS bạn có thể lưu khóa trong Keychain của hệ thống thay vì một file `.env`. Skill tự động lấy chúng lên như nguồn ưu tiên thấp nhất — file `.env` và môi trường tiến trình vẫn thắng khi xung đột.

```bash
# Interactive setup — prompts for each known key, skip with empty input
skills/last30days/scripts/setup-keychain.sh

# Or store a single key by hand
security add-generic-password -a "$USER" -s last30days-XAI_API_KEY -w "xai-..."

# Inspect / clean up
skills/last30days/scripts/setup-keychain.sh --list
skills/last30days/scripts/setup-keychain.sh --delete XAI_API_KEY
```

Các mục được lưu dưới tên dịch vụ `last30days-<KEY>` cho người dùng hiện tại. Trên các nền tảng không phải Darwin, trình tải là no-op, nên không có thay đổi hành vi cho người dùng Linux/Windows.

Đã có khóa dưới các tên dịch vụ Keychain khác? Hãy đặt ánh xạ `LAST30DAYS_KEYCHAIN_ALIASES` không bí mật được mô tả trong [CONFIGURATION.md](CONFIGURATION.md#reusing-existing-macos-keychain-items) thay vì sao chép bí mật.

Xem [CONFIGURATION.md](CONFIGURATION.md) để biết ma trận khóa đầy đủ theo nguồn, ưu tiên nhà cung cấp suy luận, và ưu tiên backend tìm kiếm web.

## Cấu hình

Hai điều bạn có thể sẽ muốn biết ngay ngày đầu:

**Nơi lưu file nghiên cứu.** `LAST30DAYS_MEMORY_DIR` mặc định là `~/Documents/Last30Days/` (Windows: `C:\Users\<you>\Documents\Last30Days\`). Ghi đè bằng cách đặt biến môi trường đó thành bất kỳ đường dẫn nào trong shell của bạn, hoặc `--save-dir <path>` mỗi lượt chạy. Dùng `--output <file>` khi bạn cần kết quả đã kết xuất tại một đường dẫn chính xác, dùng định dạng được chọn bởi `--emit`. Dùng `--save-suffix=<name>` để giữ nhiều biến thể của cùng một chủ đề riêng biệt (ví dụ theo khách hàng). Mỗi lượt chạy `--save-dir` tạo ra `<slug>-raw[-suffix].md`. Tuỳ chọn chạy `python3 skills/last30days/scripts/last30days.py --preflight` để xem lại các lần ghi dự kiến mà không bắt đầu nghiên cứu.

**Đầu ra có cấu trúc cho agent và workflow.** Hãy yêu cầu `/last30days` cho JSON có thể đọc bằng máy để nhận hồ sơ agent ổn định, có phiên bản. Để dùng engine trực tiếp trong script hoặc phát triển, chạy `python3 skills/last30days/scripts/last30days.py "AI coding agents" --emit=json`; chỉ thêm `--json-profile=raw` khi bạn cần bản dump `Report` nội bộ không phiên bản. Xem [JSON export field reference and versioning policy](docs/reference/json-export.md).

**Khám phá không cần chủ đề.** Hãy hỏi `/last30days what's trending in AI agents?` để nhận một bản tóm tắt khám phá đã xếp hạng thay vì nghiên cứu một chủ đề bạn đã biết - trên một host agent, việc này chạy giao thức ba lệnh do host phán xét (mô hình nêu tên chủ đề, lọc rác, chấm điểm mức xứng đáng, và viết các góc độ nội dung). Để dùng engine trực tiếp trong script hoặc cron, chạy `python3 skills/last30days/scripts/last30days.py --discover "AI agents"` (một lần: tên chủ đề xác định, không có góc độ); thêm `--emit=json` cho hợp đồng khám phá có phiên bản. Khám phá loại trừ lẫn nhau với một chủ đề vị trí và `--drill`.

**Theo dõi xu hướng qua các lượt chạy.** Chế độ mặc định tạo một ảnh chụp markdown mới cho mỗi lượt chạy. Để tích lũy phát hiện theo thời gian, thêm `--store` để lưu vào cơ sở dữ liệu SQLite, rồi dùng [`scripts/watchlist.py`](skills/last30days/scripts/watchlist.py) cho các lượt chạy theo lịch (với tuỳ chọn gửi Slack / webhook khi có phát hiện mới) và [`scripts/briefing.py`](skills/last30days/scripts/briefing.py) cho bản tóm tắt hàng ngày / hàng tuần. Mẫu nhịp đầy đủ nằm trong [CONFIGURATION.md](CONFIGURATION.md#trend-monitoring-store--watchlist--briefings).

**Một thư viện nghiên cứu có thể đăng ký theo dõi.** Hãy yêu cầu `/last30days` xây dựng feed thư viện của bạn, hoặc dùng `python3 skills/last30days/scripts/last30days.py library feed` trực tiếp cho script và phát triển. Nó biến các bản tóm tắt đã lưu thành `index.html`, một `feed.xml` Atom cục bộ, và các trang tóm tắt dễ đọc. Chỉ thêm `--publish` khi bạn muốn trang chỉ mục HTML và các trang tóm tắt được lưu trữ; xuất bản là tuỳ chọn tham gia tường minh và mặc định công khai. Để feed Atom có thể đăng ký theo dõi, hãy lưu trữ thư mục đầu ra đã tạo trên một host tĩnh như GitHub Pages.

**Tìm kiếm mọi thứ bạn đã nghiên cứu.** Hãy hỏi `/last30days search my library for MCP servers` hoặc `/last30days have I researched MCP servers before?`. Để dùng engine trực tiếp, chạy `python3 skills/last30days/scripts/last30days.py library search "MCP servers"`. Tìm kiếm ngoại tuyến và xác định: nó lập chỉ mục tăng dần cùng các bản tóm tắt đã lưu dùng cho feed thư viện, hợp nhất các lần bắt gặp trong store theo lượt chạy khớp, và nhóm kết quả theo chủ đề và ngày. Các lượt chạy mới cũng hiển thị một mục **From your library** gọn khi nghiên cứu trước đó chồng lấp chủ đề hiện tại; đặt `LAST30DAYS_LIBRARY_CONTEXT=off` để tắt ngữ cảnh thụ động đó.

Các script bao bọc theo client, subreddit ngang hàng danh mục tùy chỉnh, và kênh beta thử nghiệm cho các tùy chỉnh đang tiến hành cũng được ghi trong [CONFIGURATION.md](CONFIGURATION.md).

## Trưng bày: các feed nghiên cứu của cộng đồng

Đã xuất bản một bản cập nhật AI định kỳ, theo dõi thị trường, hay một nỗi ám ảnh hẹp tuyệt vời với last30days? Chia sẻ URL thư viện công khai—hoặc URL Atom sau khi lưu trữ `feed.xml` trên một host tĩnh—trong [the community showcase thread](https://github.com/mvanhorn/last30days-skill/issues/532). Các feed cộng đồng sẽ được liên kết ở đây khi chủ sở hữu gửi chúng; thread đó là điểm tập hợp trong lúc chờ đợi.

## Cách hoạt động

1. **Bạn gõ một chủ đề.** Con người, công ty, sản phẩm, công nghệ, "X vs Y." Bất cứ thứ gì.
2. **Agent phân giải ai là quan trọng.** Tìm các handle X (bao gồm nhà sáng lập), repo GitHub, subreddit, hashtag TikTok, kênh YouTube. Với "Kanye West" nó biết r/hiphopheads, @kanyewest, và "bully review" trên YouTube. Với "OpenClaw" nó phân giải openclaw/openclaw trên GitHub và lấy số sao trực tiếp.
3. **Mọi nguồn được tìm kiếm song song.** Mở rộng đa truy vấn. Kết quả được chấm điểm theo tương tác, độ liên quan, độ mới.
4. **Chiều sâu không ai khác có.** Toàn bộ bản ghi chép YouTube từ video phản ứng. Bình luận Reddit hàng đầu với số upvote. Chú thích TikTok. Tỷ lệ cược Polymarket. Không chỉ tiêu đề và liên kết.
5. **Cùng một câu chuyện, được hợp nhất.** Wireless Festival công bố trên Reddit, thảo luận trên X, giá vé trên TikTok = một cụm, không phải ba mục riêng biệt.
6. **Tổng hợp thành một bản tóm tắt.** Dựa trên dữ liệu cụ thể. Trích dẫn theo nguồn. Xếp hạng theo những gì mọi người thực sự tương tác. Không phải "here's what I found." Mà là "here's what matters."
7. **Rồi nó trở thành chuyên gia của bạn.** Sau một lượt chạy, phiên Claude của bạn biết mọi thứ cộng đồng biết. Hỏi câu hỏi tiếp nối. Bảo nó viết prompt, soạn email, lên kế hoạch chuyến đi, kiến trúc hệ thống - tất cả dựa trên những gì có thật ngay lúc này.

## Mọi người nói gì

> "Tôi tìm thấy một skill Claude Code nghiên cứu bất kỳ chủ đề nào trên Reddit, X, YouTube và HN từ 30 ngày qua. Rồi còn viết prompt cho bạn. Tôi đã tự tìm kiếm trên Reddit và X để nghiên cứu trước mỗi nội dung mình viết. Từng tab một. Từng thread một. Đó là phần mất 90 phút. Công cụ này loại bỏ nó." -@itsjasonai

> "Một skill duy nhất này thay thế toàn bộ quy trình nghiên cứu của tôi. Bạn đưa cho nó một chủ đề, nó quét Reddit, X và web để tìm những gì mọi người thực sự đang nói. Không phải bài blog cũ. Những cuộc trò chuyện thật từ 30 ngày qua." -@itswilsoncharles

> "5 trong 10 repo trending trên GitHub hôm nay là công cụ Claude. #1: mvanhorn/last30days-skill" -@yieldhunter95

## Mã nguồn mở

Giấy phép MIT. Không theo dõi. Không phân tích. Nghiên cứu của bạn ở lại trên máy của bạn (xem Xử lý dữ liệu bên dưới).

Để biết lệnh kiểm tra, xem [CONTRIBUTING.md](CONTRIBUTING.md#tests).

Được xây dựng bằng Python 3.12+, yt-dlp, Node.js (client Bird được vendor cho tìm kiếm X), và ScrapeCreators API. Kiến trúc engine v3 bởi [@j-sperling](https://github.com/j-sperling).

Xem [CONTRIBUTING.md](CONTRIBUTING.md) để mở một PR, [CONTRIBUTORS.md](CONTRIBUTORS.md) cho danh sách đầy đủ các cộng tác viên cộng đồng, và [CHANGELOG.md](CHANGELOG.md) cho lịch sử phiên bản.

## Xử lý dữ liệu

Theo mặc định skill chạy hoàn toàn trên máy của bạn — không dữ liệu nào rời đi ngoại trừ tới các API tìm kiếm bạn gọi (Reddit, ScrapeCreators, v.v.).

**Xếp hạng lại.** Khi bạn cấu hình một nhà cung cấp suy luận (`OPENAI_API_KEY`, `XAI_API_KEY`, `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, hoặc `GOOGLE_GENAI_API_KEY`), engine gửi chủ đề nghiên cứu và các đoạn nội dung đã truy xuất của bạn tới nhà cung cấp đó để chấm điểm độ liên quan. Đây là tuỳ chọn tham gia: nếu bạn không đặt khóa suy luận nào, engine quay lại một mô hình chấm điểm cục bộ và không có cuộc gọi xếp hạng lại bên ngoài nào được thực hiện. Xem [CONFIGURATION.md](CONFIGURATION.md) để biết ưu tiên nhà cung cấp.

**Kho lưu cục bộ.** Khi `LAST30DAYS_STORE=1` được đặt, các phát hiện được lưu vào một cơ sở dữ liệu SQLite cục bộ tên `research.db` bên trong thư mục lưu hiệu lực (`--save-dir` được ưu tiên hơn `LAST30DAYS_MEMORY_DIR`). Nếu không có thư mục lưu nào hiệu lực, cơ sở dữ liệu là `~/.local/share/last30days/research.db`. Bật store không tải cơ sở dữ liệu của nó lên một dịch vụ từ xa. Xem [CONFIGURATION.md](CONFIGURATION.md#--store-flag) để biết chi tiết lưu giữ và hướng dẫn xóa, bao gồm dừng người dùng cơ sở dữ liệu trước khi xóa.

## Lịch sử Star

<a href="https://star-history.dera.page/#mvanhorn/last30days-skill&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
    <img alt="Star History Chart" src="https://star-history.dera.page/svg?repos=mvanhorn/last30days-skill&type=Date" />
  </picture>
</a>

---

**@slashlast30days** · [github.com/mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill)
