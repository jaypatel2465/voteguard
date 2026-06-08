"""
PDF Generator Module
Generates voter acknowledgement slip PDF files using reportlab
"""
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.pdfgen import canvas
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
import hashlib
from datetime import datetime
import os
from config import Config
from modules.security import mask_aadhar

class VoterReceiptPDFGenerator:
    def __init__(self):
        """Initialize PDF generator"""
        self.receipts_folder = Config.RECEIPTS_FOLDER
    
    def _generate_hash(self, user_id, candidate_id, timestamp):
        """Generate unique verification hash for receipt"""
        data = f"{user_id}{candidate_id}{timestamp}"
        return hashlib.sha256(data.encode()).hexdigest()[:16].upper()
    
    def generate_receipt(self, voter_name, aadhar_last4, candidate_name, party_name, user_id, candidate_id, receipt_url_base=None):
        """
        Generate voter acknowledgement slip PDF
        Args:
            voter_name: Voter's full name
            aadhar_last4: Voter's Aadhaar last 4 digits
            candidate_name: Name of candidate voted for
            party_name: Party name
            user_id: User ID for hash generation
            candidate_id: Candidate ID for hash generation
            receipt_url_base: Base URL for receipt verification QR
        Returns:
            Dict with 'success', 'pdf_path', and 'receipt_hash'
        """
        try:
            # Generate timestamp and hash
            timestamp = datetime.now()
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            receipt_hash = self._generate_hash(user_id, candidate_id, timestamp_str)
            
            # Build receipt verification URL for QR (optional)
            receipt_verify_url = None
            if receipt_url_base:
                receipt_verify_url = f"{receipt_url_base.rstrip('/')}/admin/verify-receipt/{receipt_hash}"

            # Create PDF filename
            pdf_filename = f"receipt_{receipt_hash}.pdf"
            pdf_path = os.path.join(self.receipts_folder, pdf_filename)
            
            # Create PDF
            c = canvas.Canvas(pdf_path, pagesize=letter)
            width, height = letter
            
            # Title
            c.setFont("Helvetica-Bold", 24)
            c.drawCentredString(width/2, height - 80, "VOTER ACKNOWLEDGEMENT SLIP")
            
            # Horizontal line
            c.setStrokeColor(colors.HexColor('#2563eb'))
            c.setLineWidth(2)
            c.line(50, height - 100, width - 50, height - 100)
            
            # Receipt details
            c.setFont("Helvetica-Bold", 14)
            c.setFillColor(colors.HexColor('#1e293b'))
            
            y_position = height - 150
            line_height = 35
            
            # Voter Name
            c.drawString(100, y_position, "Voter Name:")
            c.setFont("Helvetica", 14)
            c.drawString(300, y_position, voter_name)
            y_position -= line_height
            
            # Aadhaar (Masked)
            c.setFont("Helvetica-Bold", 14)
            c.drawString(100, y_position, "Aadhaar Number:")
            c.setFont("Helvetica", 14)
            c.drawString(300, y_position, mask_aadhar(aadhar_last4))
            y_position -= line_height
            
            # Date & Time
            c.setFont("Helvetica-Bold", 14)
            c.drawString(100, y_position, "Date & Time:")
            c.setFont("Helvetica", 14)
            c.drawString(300, y_position, timestamp_str)
            y_position -= line_height * 1.5
            
            # Voted For section
            c.setFont("Helvetica-Bold", 16)
            c.setFillColor(colors.HexColor('#2563eb'))
            c.drawString(100, y_position, "VOTED FOR")
            y_position -= line_height
            
            # Candidate details
            c.setFont("Helvetica-Bold", 14)
            c.setFillColor(colors.HexColor('#1e293b'))
            c.drawString(100, y_position, "Candidate:")
            c.setFont("Helvetica", 14)
            c.drawString(300, y_position, candidate_name)
            y_position -= line_height
            
            c.setFont("Helvetica-Bold", 14)
            c.drawString(100, y_position, "Party:")
            c.setFont("Helvetica", 14)
            c.drawString(300, y_position, party_name)
            y_position -= line_height * 1.5
            
            # Verification hash
            c.setFont("Helvetica-Bold", 12)
            c.drawString(100, y_position, "Verification Hash:")
            c.setFont("Courier", 12)
            c.setFillColor(colors.HexColor('#7c3aed'))
            c.drawString(100, y_position - 20, receipt_hash)

            # QR Code (optional)
            if receipt_verify_url:
                try:
                    qr_code = qr.QrCodeWidget(receipt_verify_url)
                    bounds = qr_code.getBounds()
                    size = 110
                    qr_width = bounds[2] - bounds[0]
                    qr_height = bounds[3] - bounds[1]
                    drawing = Drawing(size, size, transform=[size / qr_width, 0, 0, size / qr_height, 0, 0])
                    drawing.add(qr_code)
                    renderPDF.draw(drawing, c, width - 160, y_position - 40)
                    c.setFont("Helvetica", 9)
                    c.setFillColor(colors.HexColor('#64748b'))
                    c.drawString(width - 175, y_position - 50, "Scan to verify")
                except Exception:
                    pass
            
            # Footer
            c.setFont("Helvetica-Oblique", 10)
            c.setFillColor(colors.HexColor('#64748b'))
            c.drawCentredString(width/2, 80, "This is a computer-generated acknowledgement slip.")
            c.drawCentredString(width/2, 65, "Please preserve this for your records.")
            c.drawCentredString(width/2, 50, "Powered by VoteGuard")
            
            # Border
            c.setStrokeColor(colors.HexColor('#e2e8f0'))
            c.setLineWidth(1)
            c.rect(40, 40, width - 80, height - 80, stroke=1, fill=0)
            
            c.save()
            
            return {
                'success': True,
                'pdf_path': pdf_path,
                'receipt_hash': receipt_hash,
                'filename': pdf_filename,
                'receipt_verify_url': receipt_verify_url
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
