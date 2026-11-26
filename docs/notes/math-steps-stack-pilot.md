# MATH_STEPS + STACK pilot: đặc tả thay đổi

Nhánh `feat/math-steps-stack-pilot` (fork `qu4ll12/learnhouse`), nằm trên
`chore/remove-podcasts-and-communities`.

## 1. Tóm tắt

Thêm loại task **MATH_STEPS**: học sinh giải phương trình từng bước, mỗi bước
được kiểm tra ngay, server chấm lại khi nộp.

| Loại đề | Bộ kiểm tra | Nhận diện |
|---|---|---|
| Bậc nhất (mặc định) | MATHCDT v1, chạy trong API | `contents` không có `checker_version` |
| Bậc hai (pilot) | STACK + Maxima, dịch vụ ngoài | `contents.checker_version = "stack_v1"` |

Phạm vi pilot bậc hai: hệ số nguyên trong khoảng −1000 đến 1000, a ≠ 0,
nghiệm thực hữu tỉ (gồm nghiệm phân số và nghiệm kép).

## 2. Luồng sử dụng

1. **Giáo viên** thêm task "Math steps" và chọn *Bậc nhất* (nhập phương
   trình tự do) hoặc *Bậc hai* (nhập a, b, c, xem trước phương trình).
   Đề ngoài phạm vi bị từ chối với lý do cụ thể.
2. **Học sinh** nhập từng bước rồi nhấn Enter. Mỗi bước nhận một trong các
   trạng thái: hợp lệ còn dang dở / giải xong / sai / cú pháp chưa hỗ trợ /
   lỗi dịch vụ (có nút *Thử lại*, giữ nguyên dòng đang nhập). Chỉ thêm được
   bước mới sau một bước hợp lệ; bước sai thì "Lấy lại bước cuối".
3. **Mở lại bài**: các bước đã lưu được hiển thị lại. Với đề STACK, cả chuỗi
   được kiểm tra bằng **một** request (không phải mỗi dòng một request).
4. **Nộp bài**: server tự chấm lại toàn bộ chuỗi từ đề gốc đã lưu. Điểm
   client gửi lên (luôn là 0) bị bỏ qua. Chấm theo kiểu tất cả hoặc không:
   mọi bước hợp lệ và bước cuối là đáp số đầy đủ.

## 3. Dữ liệu

```jsonc
// AssignmentTask.contents, đề STACK
{ "checker_version": "stack_v1", "equation": "x^2-5*x+6=0",
  "stack_coefficients": [1, -5, 6], "explanation": "…" }
// AssignmentTask.contents, đề bậc nhất
{ "equation": "2(x+3)=10", "explanation": "…" }
// AssignmentTaskSubmission.task_submission
{ "steps": ["(x-2)*(x-3)=0", "x=2 or x=3"] }
```

- `equation` của đề STACK phải trùng với phương trình sinh ra từ a, b, c.
  Định nghĩa câu hỏi gửi STACK do server tự sinh từ a, b, c; giáo viên không
  gửi được nội dung tuỳ ý cho STACK.
- Học sinh nhận được `checker_version` nhưng **không** nhận `stack_*`.
- Migration `m4t5h6s7t8e9` thêm giá trị enum `MATH_STEPS`. Downgrade không gỡ
  được giá trị enum (giới hạn của Postgres).

## 4. API

**`POST /assignments/{a}/tasks/{t}/check_step`** `{ previous, current }` →
`{ status, feedback, reason, example, terminal }`

- Đề bậc nhất: `previous` là dòng ngay trước.
- Đề STACK: `previous` = phương trình gốc + các bước trước, nối bằng `\n`;
  `current` = bước mới. Bắt buộc đăng nhập.
- `status`: `valid` | `invalid` | `unsupported` | `check_failed`;
  `terminal = true` khi đã giải xong.
- Chỉ để phản hồi, không ảnh hưởng điểm.

**Tạo/sửa task**: server kiểm tra cấu hình và trả 400 kèm lý do:
không có nghiệm thực, nghiệm không hữu tỉ, phương trình bậc nhất không hợp
lệ hoặc đã là đáp số, server chưa cấu hình STACK. Đề bậc nhất cũ chỉ bị kiểm
tra lại khi phương trình thay đổi.

## 5. Khi STACK lỗi hoặc quá tải

| Tình huống | Kết quả |
|---|---|
| Kiểm tra bước khi STACK lỗi | `check_failed`, học sinh bấm *Thử lại* |
| Nộp bài (tự chấm) khi STACK lỗi | Bài giữ trạng thái SUBMITTED, không bị chấm 0 |
| Giáo viên chốt điểm tay khi STACK lỗi | 503, không chốt; giáo viên thử lại hoặc chấm tay task đó |
| Bài SUBMITTED bị hoãn | Job chấm lại tự xử lý (mục 6) |

## 6. Bảo vệ tải

- **Rate limit**: tối đa 30 lần kiểm tra STACK mỗi phút cho mỗi học sinh;
  vượt thì trả 429 kèm `Retry-After`. Đề bậc nhất không bị giới hạn.
- **Giới hạn số lượt gọi STACK cùng lúc**: tối đa `STACK_MAX_CONCURRENCY`
  (mặc định 10) lượt mỗi worker API. Khi đầy, kiểm tra bước báo lỗi dịch vụ
  ngay thay vì xếp hàng; chấm bài thì chờ tối đa 8 giây để có chỗ. Mục đích:
  quá tải thì chỉ người đến sau bị lỗi, thay vì cả lớp cùng quá thời gian.
  Đo trên máy local với 40 lượt kiểm tra cùng lúc: trước khi có giới hạn,
  mọi lượt mất 8–14 giây và phần lớn lỗi; sau khi có giới hạn, 10 lượt hợp
  lệ trong khoảng 3 giây, 30 lượt báo lỗi ngay sau khoảng 0,5 giây.
- **Job chấm lại**: 10 phút một lần (bắt đầu sau 2 phút từ khi khởi động),
  chấm lại tối đa 50 bài SUBMITTED bị hoãn vì STACK hoặc Judge0 lỗi, rồi gửi
  webhook, thông báo cho học sinh và kiểm tra cấp chứng chỉ như lúc nộp.
  Có khoá Redis để nhiều worker không chạy trùng.

## 7. Cấu hình

| Biến môi trường | Ý nghĩa |
|---|---|
| `STACK_API_URL` | URL đầy đủ của endpoint STACK `/grade`. Không đặt thì không tạo được đề bậc hai. |
| `STACK_MAX_CONCURRENCY` | Số lượt gọi STACK cùng lúc mỗi worker, mặc định 10 |
| `LEARNHOUSE_ASSIGNMENT_REGRADE_DISABLED` | Đặt bất kỳ giá trị nào để tắt job chấm lại |

Redis cần được cấu hình để khoá của job chấm lại và rate limit có hiệu lực
giữa các worker.

## 8. Cú pháp cho học sinh (đề STACK)

Theo Maxima: `*` cho phép nhân, `^` cho luỹ thừa, `or` giữa các nghiệm.
Ví dụ: `(x-2)*(x-3)=0` rồi `x=2 or x=3`.

## 9. Kiểm thử

```bash
cd apps/api
uv run pytest src/tests/services/math_steps src/tests/routers/test_assignments_router_wiring.py -q
STACK_LIVE_URL=http://127.0.0.1:3080/grade uv run pytest src/tests/services/math_steps/test_stack_live.py -q
cd ../web && bun test --isolate ./tests/math-steps-stack.test.mjs
```

Test live bị bỏ qua nếu không đặt `STACK_LIVE_URL`. Đã kiểm tra thêm trên
máy local: migration trên Postgres thật, khoá Redis, rate limit với Redis
thật, và giao diện tiếng Việt ở màn hình rộng 390px và desktop bằng trình
duyệt.

## 10. File chính

| Phần | File |
|---|---|
| Bộ kiểm tra STACK | `apps/api/src/services/courses/activities/math_steps/stack_v1.py` |
| Bộ kiểm tra bậc nhất | `apps/api/src/services/courses/activities/math_steps/v1_checker.py`, `terminal.py` |
| Kiểm tra cấu hình, check_step, chấm điểm | `apps/api/src/services/courses/activities/assignments.py` |
| Endpoint | `apps/api/src/routers/courses/assignments.py` |
| Job chấm lại | `apps/api/src/services/courses/assignment_regrade_scheduler.py` |
| Migration | `apps/api/migrations/versions/m4t5h6s7t8e9_add_math_steps_assignment_type.py` |
| Giao diện | `apps/web/app/orgs/[orgslug]/dash/assignments/[assignmentuuid]/_components/TaskEditor/Subs/TaskTypes/TaskMathStepsObject.tsx` |
| Helper phía client | `apps/web/lib/assignments/mathSteps.ts` |
| Test | `apps/api/src/tests/services/math_steps/`, `apps/web/tests/math-steps-stack.test.mjs` |

## 11. Giới hạn đã biết và việc còn lại

- **Cần staging**: đo sức tải thật trên amd64 để chọn `STACK_MAX_CONCURRENCY`,
  `GOEMAXIMA_QUEUE_LEN` và số container; kiểm tra mạng API → STACK; chạy
  migration theo quy trình deploy thật; thử nhiều pod; thử trên điện thoại.
- Rate limit có thể lọt thêm khoảng 1 lượt mỗi tiến trình API khi nhiều
  request đến đúng cùng lúc (bộ giới hạn dùng chung có từ trước).
- STACK ngừng lâu với trên 50 bài bị hoãn thì các bài mới hơn phải chờ đến
  khi STACK hoạt động lại.
- Đổi cấu hình chấm điểm của bài tập sẽ chấm lại mọi bài qua STACK ngay trong
  request của giáo viên; với lớp đông, request này có thể chậm.
- Mới dịch tiếng Việt, tiếng Anh và tiếng Ả Rập; các ngôn ngữ khác hiện tiếng Anh.
- Phương trình hiển thị theo cú pháp Maxima (`x^2`, `*`), chưa hiển thị dạng toán học.
- Tool AI chỉ tạo được đề bậc nhất.
