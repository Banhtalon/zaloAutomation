        function openImageModal(url) {
            if (!url) {
                alert('Không có ảnh chụp báo cáo cho lớp này.');
                return;
            }
            document.getElementById('modal-image').src = url;
            document.getElementById('image-modal').classList.remove('hidden');
        }

        function closeImageModal() {
            document.getElementById('image-modal').classList.add('hidden');
            document.getElementById('modal-image').src = "";
        }
