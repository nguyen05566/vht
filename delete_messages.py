#!/usr/bin/env python3
"""
Xóa tất cả tin nhắn (inbox và sent) của tài khoản gamevh.net
Sử dụng khi cần dọn dẹp tin nhắn nhận xu để tránh bị server phát hiện gom xu
"""

import requests
import re
import sys
import os

def delete_all_messages(username, password):
    """
    Xóa tất cả tin nhắn của tài khoản gamevh.net
    
    Args:
        username: Tên đăng nhập
        password: Mật khẩu
    
    Returns:
        bool: True nếu thành công, False nếu thất bại
    """
    
    print(f"🔐 Đang đăng nhập tài khoản: {username}...")
    
    # Tạo session
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'})
    
    try:
        # Bước 1: Đăng nhập
        s.get('https://gamevh.net/login.jsp', timeout=30)
        resp = s.post('https://gamevh.net/login.jsp', timeout=30,
            data={'redirect': '/', 'USER_NAME': username, 'PASSWORD': password,
                  'AUTO_LOGIN': 'true', 'LOGIN': 'Đăng nhập'},
            headers={'Origin': 'https://gamevh.net', 'Referer': 'https://gamevh.net/login.jsp'},
            allow_redirects=True)
        
        if 'login.jsp' in resp.url:
            print("❌ Đăng nhập thất bại! Kiểm tra lại username/password")
            return False
        
        print("✅ Đăng nhập thành công")
        
        # Bước 2: Kiểm tra số tin nhắn trước khi xóa
        pm_resp = s.get('https://gamevh.net/com/ftl/game/pm/pm.jsp', timeout=30)
        badge_match = re.search(r'Tin nhắn.*?<span[^>]*>(\d+)</span>', pm_resp.text, re.DOTALL)
        if badge_match:
            msg_count = badge_match.group(1)
            print(f"📬 Số tin nhắn hiện tại: {msg_count}")
        
        # Bước 3: Xóa tất cả tin nhắn đến (inbox)
        print("\n🗑️ Đang xóa TẤT CẢ tin nhắn đến...")
        delete_inbox_url = 'https://gamevh.net/com/ftl/game/pm/pm_remove_all.jsp?position=inbox'
        delete_resp = s.get(delete_inbox_url, timeout=120, 
                          headers={'Referer': 'https://gamevh.net/com/ftl/game/pm/pm.jsp'})
        
        if delete_resp.status_code == 200:
            print("✅ Đã xóa tin nhắn đến (inbox)")
        else:
            print(f"⚠️ Lỗi khi xóa inbox: Status {delete_resp.status_code}")
        
        # Bước 4: Xóa tất cả tin nhắn đã gửi (sent)
        print("\n🗑️ Đang xóa TẤT CẢ tin nhắn đã gửi...")
        delete_sent_url = 'https://gamevh.net/com/ftl/game/pm/pm_remove_all.jsp?position=sent'
        delete_resp = s.get(delete_sent_url, timeout=120,
                          headers={'Referer': 'https://gamevh.net/com/ftl/game/pm/pm.jsp'})
        
        if delete_resp.status_code == 200:
            print("✅ Đã xóa tin nhắn đã gửi (sent)")
        else:
            print(f"⚠️ Lỗi khi xóa sent: Status {delete_resp.status_code}")
        
        # Bước 5: Kiểm tra lại sau khi xóa
        print("\n📊 Kiểm tra lại...")
        pm_resp = s.get('https://gamevh.net/com/ftl/game/pm/pm.jsp', timeout=30)
        badge_match = re.search(r'Tin nhắn.*?<span[^>]*>(\d+)</span>', pm_resp.text, re.DOTALL)
        
        if badge_match:
            remaining = badge_match.group(1)
            print(f"⚠️ Còn {remaining} tin nhắn (có thể là tin mới)")
        else:
            print("✅ Đã xóa sạch tất cả tin nhắn!")
        
        print("\n🎉 HOÀN TẤT!")
        return True
        
    except Exception as e:
        print(f"❌ Lỗi: {e}")
        return False


if __name__ == "__main__":
    # Lấy username/password từ environment variables hoặc arguments
    username = os.environ.get('USERNAME') or (sys.argv[1] if len(sys.argv) > 1 else None)
    password = os.environ.get('PASSWORD') or (sys.argv[2] if len(sys.argv) > 2 else None)
    
    if not username or not password:
        print("❌ Thiếu thông tin!")
        print("Cách dùng:")
        print("  python delete_messages.py <username> <password>")
        print("Hoặc set environment variables:")
        print("  export USERNAME=your_username")
        print("  export PASSWORD=your_password")
        print("  python delete_messages.py")
        sys.exit(1)
    
    success = delete_all_messages(username, password)
    sys.exit(0 if success else 1)
