#!/bin/bash
# Sửa tất cả arena files

for file in arena*.py; do
    echo "Đang sửa $file..."
    
    # 1. BOT_BET_XU = 500
    sed -i 's/BOT_BET_XU = 5000/BOT_BET_XU = 500/' "$file"
    
    # 2. Hàm get_1k_to_5k_bet_objs: đổi 5000-10000 thành 500-1000
    sed -i 's/5000 <= ba\["value"\] <= 10000/500 <= ba\["value"\] <= 1000/' "$file"
    
    # 3. Hàm resolve_bet_amt_id: đổi 5000-10000 thành 500-1000
    sed -i 's/5000 <= ba\["value"\] <= 10000/500 <= ba\["value"\] <= 1000/' "$file"
    
    # 4. Log text: 50% → 20%
    sed -i 's/Chuyển 50% x cho xxxx/Chuyển 20% x về tài khoản đích/' "$file"
    
    # 5. Log text: Dò bàn 500-10k → 500-1k
    sed -i 's/tìm bàn 500-10k/tìm bàn 500-1000 xu/' "$file"
    sed -i 's/dò bàn 500-10k/dò bàn 500-1000 xu/' "$file"
    
    # 6. Log text: TẠO BÀN MỚI (nếu cần)
    # Giữ nguyên
    
done

echo "Hoàn tất!"
