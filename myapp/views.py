from itertools import product
from multiprocessing import context
from django.contrib import messages
import random
from decimal import Decimal
from django.shortcuts import redirect, render, get_object_or_404
from django.http import HttpResponse, JsonResponse
from .models import Customers, Categories, Employees, Ingredients, OrderDetails, Products, Suppliers, Orders, Payments, Recipes
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.urls import reverse

# Create your views here.
def home(request):
    # แสดงเฉพาะเมนูที่สถานะเปิดอยู่ (True) เท่านั้น
    menu = Products.objects.filter(is_active='True').order_by('category')
    product_type = Categories.objects.all()
    
    # ระบบเช็คสต็อกก่อนแสดงหน้าเว็บ
    for product in menu:
        # ดึงสูตรทั้งหมดของสินค้านี้มาดู
        recipes = Recipes.objects.filter(product=product)
        product.is_available = True  # ตั้งต้นให้พร้อมขายไว้ก่อน
        
        for recipe in recipes:
            # ดึงจำนวนสต็อกปัจจุบัน (ถ้าเป็น None ให้ถือว่าเป็น 0)
            current_stock = recipe.ingredient.stock_qty or Decimal('0')
            # ปริมาณที่ต้องใช้ต่อ 1 หน่วย
            needed_per_unit = recipe.quantity_used or Decimal('0')
            
            # ถ้าสต็อกในโกดัง น้อยกว่า ที่ต้องใช้ชง 1 แก้ว
            if current_stock < needed_per_unit:
                product.is_available = False
                break # เจออันเดียวที่ไม่พอก็ถือว่าทำไม่ได้เลย
                
    context = {
        'menu': menu,
        'product_type': product_type
    }
    return render(request, 'index.html', context=context)

def _get_pending_order_from_session(request):
    order_id = request.session.get('current_order_id')
    if not order_id:
        return None
    return Orders.objects.filter(order_id=order_id, order_status='Pending').first()

def payment(request):
    order = _get_pending_order_from_session(request)
    order_items = []
    total = 0
    menu = Products.objects.all()

    if order:
        order_items = OrderDetails.objects.filter(order=order)
        total = sum((item.unit_price or 0) * (item.quantity or 0) for item in order_items)

    member_info = order.customer if order and order.customer else None

    return render(request, 'payment.html', {
        'order': order,
        'order_items': order_items,
        'total': total,
        'menu': menu,
        'member_info': member_info,
    })

# AJAX endpoint to check if a customer exists based on phone number
# def check_member(request):
#     if request.method != 'POST':
#         return JsonResponse({'error': 'Method not allowed'}, status=405)
#
#     phone = request.POST.get('phone', '').strip()
#     if not phone:
#         return JsonResponse({'found': False, 'message': 'กรุณากรอกหมายเลขโทรศัพท์'}, status=400)
#
#     customer = Customers.objects.filter(cus_phone=phone).first()
#     order = _get_pending_order_from_session(request)
#
#     if customer:
#         if order:
#             order.customer = customer
#             order.save()
#
#         return JsonResponse({
#             'found': True,
#             'name': customer.cus_name or '-',
#             'points': customer.member_points or 0,
#             'message': 'พบสมาชิกแล้ว',
#         })
#
#     return JsonResponse({'found': False, 'message': 'ไม่พบข้อมูลลูกค้า'}, status=404)

# Function to remove an item from the order
def remove_order_item(request, item_id):
    item = get_object_or_404(OrderDetails, pk=item_id)
    order = item.order
    item.delete()
    order.total_amount = sum(od.unit_price * od.quantity for od in OrderDetails.objects.filter(order=order)) or 0
    order.save()
    return redirect('payment')

def orderdetail(request):
    ingredients = Ingredients.objects.filter(unit='Topping')
    existing_order = _get_pending_order_from_session(request)
    existing_items = []
    if existing_order:
        existing_items = OrderDetails.objects.filter(order=existing_order)

    if request.method == 'POST':
        product_name = request.POST.get('product_name')
        quantity = int(request.POST.get('quantity'))
        final_price = float(request.POST.get('final_price'))
        drink_type = request.POST.get('drink_type')
        size = request.POST.get('size')
        sweetness = request.POST.get('sweetness')
        allergy = "แพ้นม/ครีม" if request.POST.get('allergy_milk') else "ไม่มีอาการแพ้"
        user_note = request.POST.get('note', '')
        selected_toppings = request.POST.getlist('toppings')
        topping_names = []
        for tid in selected_toppings:
            ing = Ingredients.objects.filter(ingredient_id=tid).first()
            if ing:
                topping_names.append(ing.ingredient_name)

        full_note = f"ประเภท: {drink_type}, ไซส์: {size}, หวาน: {sweetness}%, ท็อปปิ้ง: {', '.join(topping_names)}, {allergy}, หมายเหตุ: {user_note}"
        product = Products.objects.filter(product_name=product_name).first()
        
        # ดึงพนักงานจาก Session ที่ล็อกอินอยู่
        emp_id = request.session.get('employee_id')
        emp = Employees.objects.filter(employee_id=emp_id).first()
        
        # ถ้าไม่ได้ล็อกอิน ให้ใช้ระบบสุ่มพนักงานขายเป็น Fallback (เพื่อความปลอดภัยของระบบ)
        if not emp:
            sales_employees = Employees.objects.filter(position='Sales')
            if not sales_employees.exists():
                sales_employees = Employees.objects.filter(position='พนักงานขาย')
            emp = random.choice(list(sales_employees)) if sales_employees.exists() else Employees.objects.first()
        
        order = _get_pending_order_from_session(request)

        if not order:
            last_order = Orders.objects.filter(order_id__startswith='OR').order_by('-order_id').first()
            next_num = int(last_order.order_id[2:]) + 1 if last_order else 1
            order_id = f"OR{next_num:04d}"
            order = Orders.objects.create(order_id=order_id, employee=emp, order_datetime=timezone.now(), total_amount=Decimal('0.00'), order_status='Pending', order_type='Walk-in')
            request.session['current_order_id'] = order.order_id

        unit_price = final_price / quantity if quantity != 0 else 0
        OrderDetails.objects.create(
            order=order, 
            product=product, 
            quantity=quantity, 
            unit_price=unit_price, 
            note=full_note,
            sweetness=int(sweetness) if sweetness else 100
        )
        order.total_amount = (order.total_amount or Decimal('0.00')) + Decimal(str(final_price))
        order.save()
        return redirect('payment')

    return render(request, 'orderdetail.html', {'ingredient': ingredients, 'existing_order': existing_order, 'existing_items': existing_items})

def login(request):
    if request.method == 'POST':
        emp_id = request.POST.get('employee_id')
        password = request.POST.get('password')
        try:
            employee = Employees.objects.get(employee_id=emp_id)
            if employee.emp_phone == password: 
                request.session['employee_id'] = employee.employee_id
                request.session['employee_name'] = employee.emp_name
                request.session['employee_position'] = employee.position
                return redirect('employee_nav')
            else:
                messages.error(request, "รหัสผ่านไม่ถูกต้อง")
        except Employees.DoesNotExist:
            messages.error(request, "ไม่พบรหัสพนักงานนี้ในระบบ")
    return render(request, 'login.html')

def employee_nav(request):
    if 'employee_id' not in request.session: return redirect('login')
    context = {
        'emp_name': request.session.get('employee_name'),
        'emp_position': request.session.get('employee_position')
    }
    return render(request, 'employee_nav.html', context)

def register_customer(request):
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    name, phone = request.POST.get('name', '').strip(), request.POST.get('phone', '').strip()
    if not name or not phone: return JsonResponse({'error': 'กรุณากรอกข้อมูลให้ครบถ้วน'}, status=400)
    if Customers.objects.filter(cus_phone=phone).exists(): return JsonResponse({'error': 'เบอร์โทรศัพท์นี้เป็นสมาชิกอยู่แล้ว'}, status=400)
    last_customer = Customers.objects.filter(customer_id__startswith='C').order_by('-customer_id').first()
    next_id_num = int(last_customer.customer_id[1:]) + 1 if last_customer else 1
    new_customer_id = f"C{next_id_num:05d}"
    try:
        customer = Customers.objects.create(customer_id=new_customer_id, cus_name=name, cus_phone=phone, member_points=0, created_at=timezone.now())
        order = _get_pending_order_from_session(request)
        if order: 
            order.customer = customer
            order.save()
        return JsonResponse({'success': True, 'message': 'สมัครสมาชิกสำเร็จ', 'customer_id': customer.customer_id, 'name': customer.cus_name, 'phone': customer.cus_phone})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def get_customer_info(request):
    phone = request.GET.get('phone', '').strip()
    if not phone:
        return JsonResponse({'error': 'กรุณากรอกเบอร์โทรศัพท์'}, status=400)
    
    customer = Customers.objects.filter(cus_phone=phone).first()
    if customer:
        return JsonResponse({
            'success': True,
            'name': customer.cus_name,
            'points': customer.member_points or 0
        })
    else:
        return JsonResponse({'success': False, 'message': 'ไม่พบข้อมูลสมาชิก'})

def submit_order(request):
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    order = _get_pending_order_from_session(request)
    if not order: return JsonResponse({'error': 'No order found'}, status=400)
    
    phone = request.POST.get('phone', '').strip()
    # Get payment method from request, default to 'Cash'
    payment_method = request.POST.get('payType', 'Cash')
    if payment_method == 'qr': 
        payment_method = 'QR Code'
    elif payment_method == 'credit': 
        payment_method = 'Credit Card'
    else: 
        payment_method = 'Cash'

    if phone:
        customer = Customers.objects.filter(cus_phone=phone).first()
        if customer and order.total_amount >= 50:
            # New rule: 1 point for every 50 baht spent
            customer.member_points = (customer.member_points or 0) + int(order.total_amount // 50) * 5
            customer.save()
            order.customer = customer

            
    # Update Order Status
    order.order_status = 'Confirmed'
    order.save()

    # --- ระบบตัดสต็อกอัตโนมัติ (Auto-Stock Deduction) ---
    order_items = OrderDetails.objects.filter(order=order)
    for item in order_items:
        # ดึงสูตรการชงของสินค้าตัวนี้
        recipes = Recipes.objects.filter(product=item.product)
        for recipe in recipes:
            ingredient = recipe.ingredient
            # ปริมาณที่ต้องใช้ทั้งหมด = (ที่ใช้ต่อแก้ว * จำนวนแก้วที่สั่ง)
            # ใช้ float() หรือ Decimal() เพื่อความแม่นยำ
            qty_used_per_unit = recipe.quantity_used or Decimal('0')
            
            # --- ฟังก์ชั่นปรับระดับความหวาน (Sweetness Level Adjustment) ---
            # ปรับปริมาณ "น้ำตาลทรายขาว" (IN0002) ตามระดับความหวานที่เลือก
            if ingredient.ingredient_id == 'IN0002':
                sweetness_factor = Decimal(str(item.sweetness or 100)) / Decimal('100')
                qty_used_per_unit = qty_used_per_unit * sweetness_factor
            # --------------------------------------------------------

            order_qty = Decimal(str(item.quantity or 0))
            total_deduction = qty_used_per_unit * order_qty
            
            # หักออกจากสต็อก (ถ้าสต็อกเป็น None ให้เริ่มที่ 0)
            current_stock = ingredient.stock_qty or Decimal('0')
            ingredient.stock_qty = current_stock - total_deduction
            
            # อัปเดตสถานะวัตถุดิบถ้าของเหลือน้อยกว่าค่าขั้นต่ำ
            min_limit = ingredient.min_qty or Decimal('0')
            if ingredient.stock_qty <= min_limit:
                ingredient.ingredirent_status = 'Low Stock'
            else:
                ingredient.ingredirent_status = 'Normal'
            
            ingredient.save()
    # -----------------------------------------------
    
    # Create Payment Record
    Payments.objects.create(
        order=order,
        payment_datetime=timezone.now(),
        payment_method=payment_method,
        amount_paid=order.total_amount,
        payment_status='Completed'
    )
    
    # Prepare data for the slip
    order_items_data = []
    for item in order_items:
        order_items_data.append({
            'name': f"{item.product.product_name} ({item.product.size})",
            'quantity': item.quantity,
            'price': float(item.unit_price),
            'note': item.note
        })
    
    points_earned = 0
    if order.customer and order.total_amount >= 50:
        points_earned = int(order.total_amount // 50) * 5

    response_data = {
        'success': True,
        'order_id': order.order_id,
        'datetime': timezone.now().strftime('%d/%m/%Y %H:%M:%S'),
        'items': order_items_data,
        'total': float(order.total_amount),
        'payment_method': payment_method,
        'customer_name': order.customer.cus_name if order.customer else 'ลูกค้าทั่วไป',
        'points_earned': points_earned,
        'redirect': reverse('home')
    }

    request.session.pop('current_order_id', None)
    return JsonResponse(response_data)

def get_order_details(request):
    order_id = request.GET.get('order_id')
    order = get_object_or_404(Orders, pk=order_id)
    order_items = OrderDetails.objects.filter(order=order)
    payment = Payments.objects.filter(order=order).first()
    
    order_items_data = []
    for item in order_items:
        order_items_data.append({
            'name': f"{item.product.product_name} ({item.product.size})",
            'quantity': item.quantity,
            'price': float(item.unit_price),
            'note': item.note
        })
    
    points_earned = int(order.total_amount // 50) * 5 if order.customer and order.total_amount >= 50 else 0

    response_data = {
        'success': True,
        'order_id': order.order_id,
        'datetime': order.order_datetime.strftime('%d/%m/%Y %H:%M:%S'),
        'items': order_items_data,
        'total': float(order.total_amount),
        'payment_method': payment.payment_method if payment else 'N/A',
        'customer_name': order.customer.cus_name if order.customer else 'ลูกค้าทั่วไป',
        'points_earned': points_earned,
    }
    return JsonResponse(response_data)

# def complete_payment(request):
#     order = _get_pending_order_from_session(request)
#     if order: 
#         order.order_status = 'Completed'
#         order.save()
#     request.session.pop('current_order_id', None)
#     return redirect('home')

def admin_manage(request):
    if 'employee_id' not in request.session: return redirect('login')
    
    search_query = request.GET.get('search', '').strip()
    
    # ดึงข้อมูลทั้งหมด
    all_products = Products.objects.all().order_by('product_id')
    all_ingredients = Ingredients.objects.all().order_by('supplier__supplier_name', 'ingredient_id')
    categories = Categories.objects.all()
    suppliers = Suppliers.objects.all()
    order_list = Orders.objects.all().order_by('-order_datetime')

    if search_query:
        order_list = order_list.filter(
            Q(order_id__icontains=search_query) | 
            Q(customer__cus_phone__icontains=search_query)
        )

    # ระบบแบ่งหน้า (Pagination) - หน้าละ 20 รายการ
    # 1. สำหรับเมนูสินค้า
    products_paginator = Paginator(all_products, 20)
    products_page = products_paginator.get_page(request.GET.get('p_page'))

    # 2. สำหรับสต็อกวัตถุดิบ
    ingredients_paginator = Paginator(all_ingredients, 20)
    ingredients_page = ingredients_paginator.get_page(request.GET.get('s_page'))

    # 3. สำหรับประวัติคำสั่งซื้อ
    orders_paginator = Paginator(order_list, 20)
    orders_page = orders_paginator.get_page(request.GET.get('page'))

    return render(request, 'admin_manage.html', {
        'products': products_page, # ส่ง page_obj ไปแทน QuerySet เดิม
        'ingredients': ingredients_page, # ส่ง page_obj ไปแทน QuerySet เดิม
        'categories': categories, 
        'suppliers': suppliers,
        'page_obj': orders_page, 
        'emp_name': request.session.get('employee_name'),
        'search_query': search_query
    })

def dashboard(request):
    if 'employee_id' not in request.session: return redirect('login')
    
    # Access control: Everyone except 'Sales' can access.
    emp_position = request.session.get('employee_position')
    if emp_position == 'Sales':
        return HttpResponse("คุณไม่มีสิทธิ์เข้าถึงหน้านี้", status=403)

    all_orders = Orders.objects.all()
    total_sales, total_orders_count = sum(order.total_amount for order in all_orders), all_orders.count()
    low_stock_count = sum(1 for ing in Ingredients.objects.all() if ing.stock_qty <= (ing.min_qty or 0))
    top_selling_items = OrderDetails.objects.values('product__product_name').annotate(total_qty=Sum('quantity')).order_by('-total_qty')[:5]
    return render(request, 'dashboard.html', {'total_sales': total_sales, 'total_orders_count': total_orders_count, 'low_stock_count': low_stock_count, 'top_selling_items': top_selling_items, 'emp_name': request.session.get('employee_name')})

def edit_product(request):
    if 'employee_id' not in request.session: return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        product = get_object_or_404(Products, pk=request.POST.get('product_id'))
        product.product_name = request.POST.get('name')
        product.price = Decimal(request.POST.get('price'))
        product.is_active = 'True' if request.POST.get('status') == 'On' else 'False'
        product.save()
        return JsonResponse({'success': True, 'message': 'บันทึกข้อมูลเรียบร้อยแล้ว'})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

import os
from django.conf import settings

def add_product(request):
    if 'employee_id' not in request.session: return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        name = request.POST.get('name')
        price = Decimal(request.POST.get('price'))
        category_id = request.POST.get('category_id')
        size = request.POST.get('size', 'S')
        status = 'True' if request.POST.get('status') == 'On' else 'False'
        
        category = get_object_or_404(Categories, pk=category_id)
        
        # Generate new product ID
        last_prod = Products.objects.filter(product_id__startswith='P').order_by('-product_id').first()
        next_id = int(last_prod.product_id[1:]) + 1 if last_prod else 1
        new_id = f"P{next_id:05d}"
        
        # Save Product to Database
        Products.objects.create(
            product_id=new_id,
            product_name=name,
            price=price,
            category=category,
            size=size,
            is_active=status
        )

        # Handle Image Upload
        if 'image' in request.FILES:
            image_file = request.FILES['image']
            # Define target path
            product_dir = os.path.join(settings.BASE_DIR, 'myapp', 'static', 'img', 'product')
            # Create directory if it doesn't exist
            os.makedirs(product_dir, exist_ok=True)
            
            # Save file as [product_id].png
            file_path = os.path.join(product_dir, f"{new_id}.png")
            with open(file_path, 'wb+') as destination:
                for chunk in image_file.chunks():
                    destination.write(chunk)

        return JsonResponse({'success': True, 'message': 'เพิ่มเมนูเรียบร้อยแล้ว'})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def edit_ingredient(request):
    if 'employee_id' not in request.session: return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        ingredient = get_object_or_404(Ingredients, pk=request.POST.get('ingredient_id'))
        supplier_id = request.POST.get('supplier_id')
        stock_qty = Decimal(request.POST.get('stock_qty'))
        min_qty = Decimal(request.POST.get('min_qty'))
        
        ingredient.supplier = get_object_or_404(Suppliers, pk=supplier_id)
        ingredient.stock_qty = stock_qty
        ingredient.min_qty = min_qty
        
        # อัปเดตสถานะอัตโนมัติ
        if ingredient.stock_qty <= ingredient.min_qty:
            ingredient.ingredirent_status = 'Low Stock'
        else:
            ingredient.ingredirent_status = 'Normal'
            
        ingredient.save()
        return JsonResponse({'success': True, 'message': 'อัปเดตข้อมูลวัตถุดิบเรียบร้อยแล้ว'})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def add_ingredient(request):
    if 'employee_id' not in request.session: return JsonResponse({'error': 'Unauthorized'}, status=401)
    if request.method != 'POST': return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        name = request.POST.get('name')
        supplier_id = request.POST.get('supplier_id')
        unit = request.POST.get('unit')
        stock_qty = Decimal(request.POST.get('stock_qty') or '0')
        min_qty = Decimal(request.POST.get('min_qty') or '0')
        
        supplier = get_object_or_404(Suppliers, pk=supplier_id)
        
        # Generate new ingredient ID (Format: IN0000)
        last_ing = Ingredients.objects.filter(ingredient_id__startswith='IN').order_by('-ingredient_id').first()
        if last_ing:
            try:
                next_id = int(last_ing.ingredient_id[2:]) + 1
            except ValueError:
                next_id = 1
        else:
            next_id = 1
        new_id = f"IN{next_id:04d}"
        
        Ingredients.objects.create(
            ingredient_id=new_id,
            ingredient_name=name,
            supplier=supplier,
            unit=unit,
            stock_qty=stock_qty,
            min_qty=min_qty,
            ingredirent_status='Available' if stock_qty > min_qty else 'Low Stock'
        )
        return JsonResponse({'success': True, 'message': 'เพิ่มวัตถุดิบเรียบร้อยแล้ว'})
    except Exception as e: return JsonResponse({'error': str(e)}, status=500)

def queue(request):
    if 'employee_id' not in request.session: return redirect('login')
    
    # All roles (Sales, Manager, Assistant Manager, Admin) can access
    pending_orders = Orders.objects.filter(order_status='Confirmed').order_by('order_datetime')
    ready_orders = Orders.objects.filter(order_status='Ready').order_by('order_datetime')
    for order in pending_orders: order.items = OrderDetails.objects.filter(order=order)
    for order in ready_orders: order.items = OrderDetails.objects.filter(order=order)
    return render(request, 'employee_state.html', {'pending_orders': pending_orders, 'ready_orders': ready_orders, 'pending_count': pending_orders.count(), 'ready_count': ready_orders.count(), 'emp_name': request.session.get('employee_name')})

def update_order_status(request, order_id, new_status):
    if 'employee_id' not in request.session: return redirect('login')
    
    order = get_object_or_404(Orders, pk=order_id)
    
    # อัปเดตพนักงานที่ดูแลออเดอร์นี้ เป็นคนที่กดเปลี่ยนสถานะล่าสุด
    emp_id = request.session.get('employee_id')
    emp = Employees.objects.filter(employee_id=emp_id).first()
    if emp:
        order.employee = emp
        
    order.order_status = new_status
    order.save()
    return redirect('queue')

def logout(request):
    request.session.flush()
    return redirect('login')
