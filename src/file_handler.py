"""
File handling and processing for the chatbot POC.
"""

import os
import base64
from typing import Dict, Any, Optional
from pathlib import Path
import fitz  # PyMuPDF
from PIL import Image


class FileHandler:
    """Handle file uploads and conversion to Claude format"""
    
    def __init__(self, uploads_dir: str):
        self.uploads_dir = uploads_dir
        os.makedirs(uploads_dir, exist_ok=True)
    
    def save_file(self, file_data: bytes, filename: str, chat_id: str) -> str:
        """
        Save uploaded file to disk.
        
        Args:
            file_data: File binary data
            filename: Original filename
            chat_id: Chat ID for organization
        
        Returns:
            Path to saved file
        """
        # Create chat-specific directory
        chat_dir = os.path.join(self.uploads_dir, chat_id)
        os.makedirs(chat_dir, exist_ok=True)
        
        # Save file
        file_path = os.path.join(chat_dir, filename)
        with open(file_path, 'wb') as f:
            f.write(file_data)
        
        return file_path
    
    def convert_to_claude_format(self, file_path: str, file_type: str) -> Optional[Dict[str, Any]]:
        """
        Convert file to Claude API format.
        
        Args:
            file_path: Path to the file
            file_type: Type of file (extension)
        
        Returns:
            Dict in Claude API format or None if error
        """
        try:
            if file_type in ['txt', 'md', 'py', 'js', 'json', 'csv']:
                return self._convert_text_file(file_path)
            
            elif file_type == 'pdf':
                return self._convert_pdf(file_path)
            
            elif file_type in ['png', 'jpg', 'jpeg', 'webp']:
                return self._convert_image(file_path, file_type)
            
            else:
                # Try as text file
                return self._convert_text_file(file_path)
        
        except Exception as e:
            print(f"Error converting file: {e}")
            return None
    
    def _convert_text_file(self, file_path: str) -> Dict[str, Any]:
        """Convert text file to Claude format"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            text = f.read()
        
        return {
            "type": "text",
            "text": text
        }
    
    def _convert_pdf(self, file_path: str) -> Dict[str, Any]:
        """Convert PDF to Claude format (base64)"""
        with open(file_path, 'rb') as f:
            pdf_data = f.read()
        
        base64_data = base64.b64encode(pdf_data).decode('utf-8')
        
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64_data
            }
        }
    
    def _convert_image(self, file_path: str, file_type: str) -> Dict[str, Any]:
        """Convert image to Claude format (base64)"""
        with open(file_path, 'rb') as f:
            image_data = f.read()
        
        base64_data = base64.b64encode(image_data).decode('utf-8')
        
        # Map file type to media type
        media_type_map = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp'
        }
        
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type_map.get(file_type, 'image/jpeg'),
                "data": base64_data
            }
        }
    
    def delete_file(self, file_path: str) -> None:
        """Delete a file from disk"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error deleting file: {e}")
    
    def delete_chat_files(self, chat_id: str) -> None:
        """Delete all files for a chat"""
        chat_dir = os.path.join(self.uploads_dir, chat_id)
        if os.path.exists(chat_dir):
            import shutil
            shutil.rmtree(chat_dir)
