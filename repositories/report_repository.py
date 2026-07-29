from datetime import datetime
from db.database import get_db_connection

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
            async with db.execute("SELECT id, student_name, class_code, submitted_at, status, error_message, job_id, action_key FROM form_submissions ORDER BY id DESC LIMIT 100") as cursor:
                rows = await cursor.fetchall()
                return [{"id": row[0], "student_name": row[1], "class_code": row[2], "submitted_at": row[3], "status": row[4], "error_message": row[5], "job_id": row[6], "action_key": row[7]} for row in rows]

    async def add_form_submission(
        self,
        student_name,
        class_code,
        status,
        error_message="",
        *,
        job_id=None,
        action_key=None,
    ):
        submitted_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with get_db_connection() as db:
            await db.execute("INSERT INTO form_submissions (student_name, class_code, submitted_at, status, error_message, job_id, action_key) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (student_name, class_code, submitted_at, status, error_message, job_id, action_key))
            await db.commit()
            
    async def save_report(self, group_name, class_code, lesson_number, lesson_date, confidence):
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO reports (group_name, class_code, lesson_number, lesson_date, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (group_name, class_code, lesson_number, lesson_date, confidence, created_at)
            )
            await db.commit()
            
    async def save_classification_log(self, message, rule_score, embedding_score, llm_result, final_result):
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with get_db_connection() as db:
            await db.execute(
                "INSERT INTO classification_logs (message, rule_score, embedding_score, llm_result, final_result, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (message, rule_score, embedding_score, llm_result, final_result, created_at)
            )
            await db.commit()
