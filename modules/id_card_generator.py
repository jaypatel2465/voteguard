"""
E-ID Card Generator Module
Generates voter E-ID card PDF using reportlab
"""
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from reportlab.lib.units import mm

# ID-1 (credit card) size in mm: 85.60 × 53.98
ID_1 = (85.60 * mm, 53.98 * mm)

import os
from config import Config
from modules.security import mask_aadhar

class VoterIDCardGenerator:
    def __init__(self):
        self.cards_folder = Config.ID_CARDS_FOLDER

    def generate_id_card(self, voter, eid_hash, receipt_url_base=None):
        """
        Generate E-ID card PDF.
        voter: dict with user info
        eid_hash: unique id hash
        receipt_url_base: base URL for QR verification
        """
        try:
            card_filename = f"eid_{eid_hash}.pdf"
            pdf_path = os.path.join(self.cards_folder, card_filename)

            c = canvas.Canvas(pdf_path, pagesize=ID_1)
            width, height = ID_1

            # Background (plain white)
            c.setFillColor(colors.white)
            c.rect(0, 0, width, height, fill=1, stroke=0)

            # Top decorative bands (orange + navy wave)
            c.setFillColor(colors.HexColor('#f59e0b'))
            c.rect(0, height - 7 * mm, width, 4 * mm, fill=1, stroke=0)

            c.setFillColor(colors.HexColor('#0f1a4a'))
            path = c.beginPath()
            path.moveTo(0, height - 8 * mm)
            path.curveTo(width * 0.3, height - 12 * mm, width * 0.7, height - 4 * mm, width, height - 10 * mm)
            path.lineTo(width, height)
            path.lineTo(0, height)
            path.close()
            c.drawPath(path, fill=1, stroke=0)

            # Bottom decorative bands
            c.setFillColor(colors.HexColor('#0f1a4a'))
            path = c.beginPath()
            path.moveTo(0, 8 * mm)
            path.curveTo(width * 0.3, 2 * mm, width * 0.7, 14 * mm, width, 6 * mm)
            path.lineTo(width, 0)
            path.lineTo(0, 0)
            path.close()
            c.drawPath(path, fill=1, stroke=0)

            c.setFillColor(colors.HexColor('#f59e0b'))
            c.rect(0, 0, width, 2.5 * mm, fill=1, stroke=0)

            # Title badge
            c.setFillColor(colors.HexColor('#f59e0b'))
            c.roundRect(42 * mm, height - 20 * mm, 36 * mm, 7 * mm, 2 * mm, fill=1, stroke=0)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(60 * mm, height - 16.8 * mm, "VOTER E-ID CARD")

            # Profile photo box
            photo_path = None
            if voter.get('profile_image'):
                photo_path = os.path.join(Config.PROFILE_PHOTOS_FOLDER, voter['profile_image'])
            if photo_path and os.path.exists(photo_path):
                c.setFillColor(colors.HexColor('#0f1a4a'))
                c.roundRect(7 * mm, height - 38 * mm, 22 * mm, 26 * mm, 2 * mm, fill=1, stroke=0)
                c.drawImage(photo_path, 8 * mm, height - 37 * mm, width=20 * mm, height=24 * mm, preserveAspectRatio=True, mask='auto')
            else:
                c.setFillColor(colors.HexColor('#0f1a4a'))
                c.roundRect(7 * mm, height - 38 * mm, 22 * mm, 26 * mm, 2 * mm, fill=1, stroke=0)
                c.setFillColor(colors.white)
                c.setFont("Helvetica", 6.5)
                c.drawCentredString(18 * mm, height - 24 * mm, "NO PHOTO")

            # Helper to fit text in a limited width
            def fit_text(text, max_width, font_name="Helvetica-Bold", font_size=7.5):
                if not text:
                    return ""
                c.setFont(font_name, font_size)
                if c.stringWidth(text, font_name, font_size) <= max_width:
                    return text
                trimmed = text
                while trimmed and c.stringWidth(trimmed + "…", font_name, font_size) > max_width:
                    trimmed = trimmed[:-1]
                return trimmed + "…"

            # Voter details (larger, dark text)
            c.setFillColor(colors.HexColor('#0f172a'))
            c.setFont("Helvetica-Bold", 7.5)
            name = f"{voter.get('first_name', '')} {voter.get('last_name', '')}".strip()
            start_x = 35 * mm
            start_y = height - 28 * mm
            line_h = 6.5 * mm
            text_max_width = width - start_x - 22 * mm - 3 * mm
            c.drawString(start_x, start_y, fit_text(f"Name: {name}", text_max_width))
            c.drawString(start_x, start_y - line_h, fit_text(f"Aadhaar: {mask_aadhar(voter.get('aadhar_last4'))}", text_max_width))
            c.drawString(start_x, start_y - 2 * line_h, fit_text(f"Phone: {voter.get('phone', '')}", text_max_width))
            c.drawString(start_x, start_y - 3 * line_h, fit_text(f"Voter ID: {voter.get('id', '')}", text_max_width))

            # Hash ID
            c.setFont("Helvetica-Bold", 6.5)
            c.setFillColor(colors.HexColor('#0f172a'))
            c.drawString(7 * mm, 9 * mm, f"ID: {eid_hash}")

            # Authority signature
            c.setFont("Helvetica-Oblique", 6.5)
            c.setFillColor(colors.HexColor('#0f172a'))
            c.drawString(start_x, 9 * mm, "Authorized Signature")

            # QR Code
            if receipt_url_base:
                verify_url = f"{receipt_url_base.rstrip('/')}/verify/{eid_hash}"
                qr_code = qr.QrCodeWidget(verify_url)
                bounds = qr_code.getBounds()
                size = 16 * mm
                qr_width = bounds[2] - bounds[0]
                qr_height = bounds[3] - bounds[1]
                drawing = Drawing(size, size, transform=[size / qr_width, 0, 0, size / qr_height, 0, 0])
                drawing.add(qr_code)
                qr_x = width - 20 * mm
                qr_y = 10 * mm
                renderPDF.draw(drawing, c, qr_x, qr_y)

            c.save()

            return {'success': True, 'pdf_path': pdf_path, 'filename': card_filename}
        except Exception as e:
            return {'success': False, 'error': str(e)}
