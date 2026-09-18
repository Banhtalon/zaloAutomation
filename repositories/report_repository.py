from db.database import get_db_connection
from core.time_utils import utc_iso

class ReportRepository:
    async def get_form_mappings(self):
        async with get_db_connection() as db:
            async with db.execute("SELECT id, question, field_type, value FROM form_mappings ORDER BY id") as cursor:
                rows = await cursor.fetchall()
                return [{"id": row[0], "question": row[1], "field_type": row[2], "value": row[3]} for row in rows]

    async def get_form_mapping_dict(self):
        mappings = await self.get_form_mappings()
        mapping_dict = {}
        for m in mappings:
            mapping_dict[m['question']] = {"type": m['field_type'], "value": m['value']}
        return mapping_dict

    async def add_form_mapping(self, question, field_type, value):
        async with get_db_connection() as db:
            await db.execute("INSERT INTO form_mappings (question, field_type, value) VALUES (?, ?, ?)", (question, field_type, value))
            await db.commit()

    async def update_form_mapping(self, mapping_id, question, field_type, value):
        async with get_db_connection() as db:
            await db.execute("UPDATE form_mappings SET question=?, field_type=?, value=? WHERE id=?", (question, field_type, value, mapping_id))
            await db.commit()

    async def delete_form_mapping(self, mapping_id):
        async with get_db_connection() as db:
            await db.execute("DELETE FROM form_mappings WHERE id=?", (mapping_id,))
            await db.commit()

    async def get_form_submissions(self):
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, student_name, class_code, submitted_at, status, "
                "error_message, job_id, action_key, screenshot_path "
                "FROM form_submissions ORDER BY id DESC LIMIT 100"
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    {
                        "id": row[0],
                        "student_name": row[1],
                        "class_code": row[2],
                        "submitted_at": row[3],
                        "status": row[4],
                        "error_message": row[5],
                        "job_id": row[6],
                        "action_key": row[7],
                        "screenshot_path": row[8],
                    }
                    for row in rows
                ]

    async def get_form_submission(self, submission_id: int):
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT id, student_name, class_code, submitted_at, status, "
                "error_message, job_id, action_key, screenshot_path "
                "FROM form_submissions WHERE id = ?",
                (submission_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "student_name": row[1],
                    "class_code": row[2],
                    "submitted_at": row[3],
                    "status": row[4],
                    "error_message": row[5],
                    "job_id": row[6],
                    "action_key": row[7],
                    "screenshot_path": row[8],
                }

    async def add_form_submission(
        self,
        student_name,
        class_code,
        status,
        error_message="",
        *,
        job_id=None,
        action_key=None,
        screenshot_path=None,
    ):
        submitted_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO form_submissions "
                "(student_name, class_code, submitted_at, status, error_message, "
                "job_id, action_key, screenshot_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    student_name,
                    class_code,
                    submitted_at,
                    status,
                    error_message,
                    job_id,
                    action_key,
                    screenshot_path,
                ),
            )
            await db.commit()

    async def delete_submission_screenshot(self, submission_id: int) -> bool:
        sub = await self.get_form_submission(submission_id)
        if not sub:
            return False
        screenshot_path = sub.get("screenshot_path")
        if screenshot_path:
            try:
                import os
                if os.path.exists(screenshot_path):
                    os.remove(screenshot_path)
            except Exception:
                pass
        async with get_db_connection() as db:
            await db.execute(
                "UPDATE form_submissions SET screenshot_path = NULL WHERE id = ?",
                (submission_id,),
            )
            await db.commit()
        return True

    async def clear_form_submissions(self) -> int:
        async with get_db_connection() as db:
            async with db.execute(
                "SELECT screenshot_path FROM form_submissions WHERE screenshot_path IS NOT NULL"
            ) as cursor:
                rows = await cursor.fetchall()
                import os
                for row in rows:
                    p = row[0]
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
            async with db.execute("DELETE FROM form_submissions") as cursor:
                deleted_count = cursor.rowcount
            await db.commit()
            return deleted_count
            
    async def save_report(self, group_name, class_code, lesson_number, lesson_date, confidence):
        created_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO reports (group_name, class_code, lesson_number, lesson_date, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (group_name, class_code, lesson_number, lesson_date, confidence, created_at)
            )
            await db.commit()
            
    async def save_classification_log(self, message, rule_score, embedding_score, llm_result, final_result):
        created_at = utc_iso()
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO classification_logs (message, rule_score, embedding_score, llm_result, final_result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message, rule_score, embedding_score, llm_result, final_result, created_at)
            )
            await db.commit()
