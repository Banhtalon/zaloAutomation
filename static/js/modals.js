        let imageModalZoomLevel = 1;
        const IMAGE_MODAL_CLICK_STEPS = [1, 1.85, 2.6];
        const IMAGE_MODAL_BUTTON_STEPS = [1, 1.4, 1.85, 2.25, 2.6];

        function applyImageModalZoom(nextZoom, clickEvent = null) {
            const numeric = Number(nextZoom);
            const clamped = Number.isFinite(numeric)
                ? Math.min(3, Math.max(1, Number(numeric.toFixed(2))))
                : 1;
            const img = document.getElementById('modal-image');
            const scrollBox = document.getElementById('modal-image-scroll');
            const label = document.getElementById('modal-image-zoom-label');
            let relX = 0.5;
            let relY = 0.35;
            if (
                clickEvent
                && img
                && typeof img.getBoundingClientRect === 'function'
                && Number.isFinite(clickEvent.clientX)
                && Number.isFinite(clickEvent.clientY)
            ) {
                const rect = img.getBoundingClientRect();
                if (rect && rect.width > 0 && rect.height > 0) {
                    relX = Math.min(1, Math.max(0, (clickEvent.clientX - rect.left) / rect.width));
                    relY = Math.min(1, Math.max(0, (clickEvent.clientY - rect.top) / rect.height));
                }
            }
            imageModalZoomLevel = clamped;
            if (label) {
                label.textContent = `${Math.round(clamped * 100)}%`;
            }
            if (!img) return;
            if (!img.style) img.style = {};
            img.style.flexShrink = '0';
            if (clamped <= 1.01) {
                img.style.width = '';
                img.style.height = '';
                img.style.maxWidth = '';
                img.style.maxHeight = '';
                img.classList.add('max-w-full', 'max-h-[82dvh]');
                img.classList.remove('cursor-zoom-out');
                img.classList.add('cursor-zoom-in');
                if (scrollBox) {
                    scrollBox.scrollTop = 0;
                    scrollBox.scrollLeft = 0;
                }
                return;
            }
            img.classList.remove('max-w-full', 'max-h-[82dvh]');
            img.style.width = `${Math.round(clamped * 100)}%`;
            img.style.height = 'auto';
            img.style.maxWidth = 'none';
            img.style.maxHeight = 'none';
            if (clamped >= IMAGE_MODAL_CLICK_STEPS[IMAGE_MODAL_CLICK_STEPS.length - 1] - 0.05) {
                img.classList.remove('cursor-zoom-in');
                img.classList.add('cursor-zoom-out');
            } else {
                img.classList.remove('cursor-zoom-out');
                img.classList.add('cursor-zoom-in');
            }
            if (scrollBox && typeof scrollBox.scrollTo === 'function') {
                const targetLeft = Math.max(0, (scrollBox.scrollWidth - scrollBox.clientWidth) * relX);
                const targetTop = Math.max(0, (scrollBox.scrollHeight - scrollBox.clientHeight) * relY);
                scrollBox.scrollTo({ left: targetLeft, top: targetTop, behavior: 'smooth' });
            } else if (scrollBox) {
                scrollBox.scrollLeft = Math.max(0, ((scrollBox.scrollWidth || 0) - (scrollBox.clientWidth || 0)) * relX);
                scrollBox.scrollTop = Math.max(0, ((scrollBox.scrollHeight || 0) - (scrollBox.clientHeight || 0)) * relY);
            }
        }

        function toggleImageModalZoom(event) {
            if (event && typeof event.stopPropagation === 'function') {
                event.stopPropagation();
            }
            let nextZoom = IMAGE_MODAL_CLICK_STEPS[0];
            for (let i = 0; i < IMAGE_MODAL_CLICK_STEPS.length; i += 1) {
                if (IMAGE_MODAL_CLICK_STEPS[i] > imageModalZoomLevel + 0.05) {
                    nextZoom = IMAGE_MODAL_CLICK_STEPS[i];
                    break;
                }
            }
            applyImageModalZoom(nextZoom, event || null);
        }

        function stepImageModalZoom(direction) {
            const dir = Number(direction) >= 0 ? 1 : -1;
            if (dir > 0) {
                for (let i = 0; i < IMAGE_MODAL_BUTTON_STEPS.length; i += 1) {
                    if (IMAGE_MODAL_BUTTON_STEPS[i] > imageModalZoomLevel + 0.05) {
                        applyImageModalZoom(IMAGE_MODAL_BUTTON_STEPS[i]);
                        return;
                    }
                }
                applyImageModalZoom(IMAGE_MODAL_BUTTON_STEPS[IMAGE_MODAL_BUTTON_STEPS.length - 1]);
                return;
            }
            for (let i = IMAGE_MODAL_BUTTON_STEPS.length - 1; i >= 0; i -= 1) {
                if (IMAGE_MODAL_BUTTON_STEPS[i] < imageModalZoomLevel - 0.05) {
                    applyImageModalZoom(IMAGE_MODAL_BUTTON_STEPS[i]);
                    return;
                }
            }
            applyImageModalZoom(1);
        }

        function resetImageModalZoom() {
            applyImageModalZoom(1);
        }

        function openImageModal(url) {
            if (!url) {
                alert('Không có ảnh chụp báo cáo cho lớp này.');
                return;
            }
            resetImageModalZoom();
            document.getElementById('modal-image').src = url;
            document.getElementById('image-modal').classList.remove('hidden');
        }

        function closeImageModal() {
            resetImageModalZoom();
            document.getElementById('image-modal').classList.add('hidden');
            document.getElementById('modal-image').src = "";
        }
