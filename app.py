import os
import csv
import io
from flask import Flask, render_template, redirect, url_for, request, flash, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from datetime import datetime, timedelta

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secretkey123'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///canteen.db'
db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# --- ТАБЛИЦА СВЯЗИ (Многие-ко-Многим) ---
# Позволяет одному ученику иметь много аллергий, а одну аллергию иметь многим ученикам
user_allergies = db.Table('user_allergies',
                          db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
                          db.Column('allergy_id', db.Integer, db.ForeignKey('allergy.id'))
                          )


# --- МОДЕЛИ ---

class Allergy(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

    def __repr__(self):
        return self.name


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    role = db.Column(db.String(50))
    balance = db.Column(db.Float, default=0.0)
    subscription_end = db.Column(db.DateTime, nullable=True)

    # Связь с аллергиями через вспомогательную таблицу
    allergies_list = db.relationship('Allergy', secondary=user_allergies, backref=db.backref('users', lazy='dynamic'))


class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)
    description = db.Column(db.String(200))
    category = db.Column(db.String(50), default='lunch')
    ingredients = db.Column(db.String(500), default='')


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'), nullable=True)
    status = db.Column(db.String(50), default='Оплачено')
    date = db.Column(db.DateTime, default=datetime.now)
    student_confirmed = db.Column(db.Boolean, default=False)
    price_paid = db.Column(db.Float, default=0.0)

    item = db.relationship('MenuItem')
    user = db.relationship('User')


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    quantity = db.Column(db.Float)
    unit = db.Column(db.String(20))


class PurchaseRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100))
    quantity = db.Column(db.Float)
    status = db.Column(db.String(50), default='На рассмотрении')
    created_at = db.Column(db.DateTime, default=datetime.now)
    cost = db.Column(db.Float, default=0.0)


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'))
    rating = db.Column(db.Integer)
    text = db.Column(db.String(500))
    date = db.Column(db.DateTime, default=datetime.now)
    item = db.relationship('MenuItem')
    user = db.relationship('User')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- ВСПОМОГАТЕЛЬНЫЕ МАРШРУТЫ ---

@app.route('/download_report')
@login_required
def download_report():
    if current_user.role != 'admin': return redirect(url_for('index'))
    si = io.StringIO()
    cw = csv.writer(si, delimiter=';')
    all_orders = Order.query.all()
    requests = PurchaseRequest.query.filter_by(status='Одобрено').all()
    income = sum(o.price_paid for o in all_orders)
    expenses = sum(r.cost for r in requests)

    cw.writerow(['ФИНАНСОВЫЙ ОТЧЕТ', datetime.now().strftime('%d.%m.%Y')])
    cw.writerow(['Выручка', 'Расходы', 'Прибыль'])
    cw.writerow([income, expenses, income - expenses])
    cw.writerow([])
    cw.writerow(['ДЕТАЛИЗАЦИЯ ТРАНЗАКЦИЙ'])
    cw.writerow(['ID', 'Дата', 'Ученик', 'Назначение', 'Сумма', 'Статус'])

    for o in all_orders:
        if o.item:
            item_name = o.item.name
        elif o.status == 'Абонемент':
            item_name = 'Покупка абонемента'
        else:
            item_name = 'Удаленное блюдо'
        cw.writerow([o.id, o.date.strftime('%d.%m.%Y %H:%M'), o.user.username, item_name, o.price_paid, o.status])

    output = make_response(si.getvalue().encode('utf-8-sig'))
    output.headers["Content-Disposition"] = "attachment; filename=report.csv"
    output.headers["Content-type"] = "text/csv"
    return output


@app.route('/remove_allergy/<int:allergy_id>')
@login_required
def remove_allergy(allergy_id):
    if current_user.role == 'student':
        # Ищем аллергию в списке пользователя по ID
        allergy_to_remove = Allergy.query.get(allergy_id)
        if allergy_to_remove and allergy_to_remove in current_user.allergies_list:
            current_user.allergies_list.remove(allergy_to_remove)
            db.session.commit()
            flash(f'Аллерген "{allergy_to_remove.name}" удален.', 'info')
    return redirect(url_for('student_dashboard'))


# --- ОСНОВНЫЕ МАРШРУТЫ ---

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin': return redirect(url_for('admin_dashboard'))
        if current_user.role == 'cook': return redirect(url_for('cook_dashboard'))
        if current_user.role == 'student': return redirect(url_for('student_dashboard'))
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Пользователь уже существует')
        else:
            new_user = User(username=username, password=password, role='student', balance=0)
            db.session.add(new_user)
            db.session.commit()
            login_user(new_user)
            return redirect(url_for('student_dashboard'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.password == password:
            login_user(user)
            return redirect(url_for('index'))
        flash('Неверный логин или пароль')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


# --- СТУДЕНТ ---
@app.route('/student', methods=['GET', 'POST'])
@login_required
def student_dashboard():
    if current_user.role != 'student': return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'buy_subscription':
            PRICE_SUB = 3000
            if current_user.balance >= PRICE_SUB:
                current_user.balance -= PRICE_SUB
                start_date = current_user.subscription_end if current_user.subscription_end and current_user.subscription_end > datetime.now() else datetime.now()
                current_user.subscription_end = start_date + timedelta(days=30)
                sub_order = Order(user_id=current_user.id, item_id=None, status='Абонемент', price_paid=PRICE_SUB,
                                  student_confirmed=True)
                db.session.add(sub_order)
                db.session.commit()
                flash('Абонемент куплен! Чек сохранен.', 'success')
            else:
                flash('Недостаточно средств', 'danger')

        # --- ЛОГИКА ДОБАВЛЕНИЯ АЛЛЕРГИИ (ОБНОВЛЕННАЯ) ---
        elif action == 'add_allergy':
            allergy_name = request.form.get('allergy_name').strip().lower()
            if allergy_name:
                # 1. Проверяем, существует ли такая аллергия глобально
                allergy_obj = Allergy.query.filter_by(name=allergy_name).first()
                if not allergy_obj:
                    # Если нет в базе - создаем новую
                    allergy_obj = Allergy(name=allergy_name)
                    db.session.add(allergy_obj)
                    db.session.commit()  # Сохраняем, чтобы получить ID

                # 2. Проверяем, есть ли она уже у юзера
                if allergy_obj not in current_user.allergies_list:
                    current_user.allergies_list.append(allergy_obj)
                    db.session.commit()
                    flash(f'Аллерген "{allergy_name}" добавлен', 'success')
                else:
                    flash('Такой аллерген уже есть в вашем списке', 'warning')

        elif action == 'buy_item':
            item = MenuItem.query.get(request.form.get('item_id'))
            if item:
                has_sub = current_user.subscription_end and current_user.subscription_end > datetime.now()
                final_price = 0 if has_sub else item.price
                if current_user.balance >= final_price:
                    current_user.balance -= final_price
                    db.session.add(
                        Order(user_id=current_user.id, item_id=item.id, status='Оплачено', price_paid=final_price))
                    db.session.commit()
                    flash('Заказ принят!', 'success')
                else:
                    flash('Недостаточно средств', 'danger')

        elif action == 'confirm_receipt':
            order = Order.query.get(request.form.get('order_id'))
            if order.user_id == current_user.id:
                order.student_confirmed = True
                order.status = 'Получено'
                db.session.commit()

    breakfasts = MenuItem.query.filter_by(category='breakfast').all()
    lunches = MenuItem.query.filter_by(category='lunch').all()
    combos = MenuItem.query.filter_by(category='combo').all()
    my_orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.date.desc()).all()
    is_subscribed = current_user.subscription_end and current_user.subscription_end > datetime.now()

    # --- СБОР ДАННЫХ ДЛЯ АВТОДОПОЛНЕНИЯ ---
    # Собираем все существующие в базе аллергии + ингредиенты из меню
    suggestions = set()

    # Добавляем все аллергии, которые уже знает система
    for a in Allergy.query.all():
        suggestions.add(a.name)

    # Добавляем ингредиенты из меню (на случай, если их еще нет в таблице Allergy)
    for item in MenuItem.query.all():
        if item.ingredients:
            parts = item.ingredients.split(',')
            for part in parts:
                suggestions.add(part.strip().lower())

    all_possible_ingredients = sorted(list(suggestions))

    return render_template('student.html',
                           breakfasts=breakfasts,
                           lunches=lunches,
                           combos=combos,
                           orders=my_orders,
                           is_subscribed=is_subscribed,
                           all_ingredients=all_possible_ingredients)


@app.route('/add_funds', methods=['POST'])
@login_required
def add_funds():
    if current_user.role != 'student': return redirect(url_for('index'))
    try:
        current_user.balance += float(request.form.get('amount'))
        db.session.commit()
        flash('Баланс пополнен', 'success')
    except:
        pass
    return redirect(url_for('student_dashboard'))


# --- ПОВАР ---
@app.route('/cook', methods=['GET', 'POST'])
@login_required
def cook_dashboard():
    if current_user.role != 'cook': return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'request_product':
            names = request.form.getlist('product_names[]')
            quantities = request.form.getlist('quantities[]')
            for name, qty in zip(names, quantities):
                if name.strip() and qty:
                    db.session.add(PurchaseRequest(product_name=name, quantity=float(qty)))
            db.session.commit()
            flash(f'Создано заявок: {len(names)} шт.', 'success')
        elif action == 'update_stock':
            prod = Product.query.get(request.form.get('product_id'))
            prod.quantity = request.form.get('quantity')
            db.session.commit()

    orders = Order.query.filter(Order.status == 'Оплачено').order_by(Order.date.desc()).all()
    food_orders = [o for o in orders if o.item_id is not None]

    products = Product.query.all()
    my_requests = PurchaseRequest.query.order_by(PurchaseRequest.created_at.desc()).limit(10).all()
    return render_template('cook.html', orders=food_orders, products=products, my_requests=my_requests)


@app.route('/update_order/<int:id>/<status>')
@login_required
def update_order(id, status):
    if current_user.role in ['cook', 'admin']:
        order = Order.query.get(id)
        order.status = status
        db.session.commit()
    return redirect(request.referrer)


# --- АДМИН ---
@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin_dashboard():
    if current_user.role != 'admin': return redirect(url_for('index'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'create_staff':
            username = request.form.get('username')
            password = request.form.get('password')
            role = request.form.get('role')
            if User.query.filter_by(username=username).first():
                flash('Пользователь уже существует!', 'danger')
            else:
                new_staff = User(username=username, password=password, role=role)
                db.session.add(new_staff)
                db.session.commit()
                flash(f'Сотрудник {username} создан.', 'success')

        elif 'request_id' in request.form:
            req = PurchaseRequest.query.get(request.form.get('request_id'))
            req.status = request.form.get('status')
            if req.status == 'Одобрено':
                req.cost = float(request.form.get('cost', 0))
                prod = Product.query.filter_by(name=req.product_name).first()
                if prod:
                    prod.quantity += req.quantity
                else:
                    db.session.add(Product(name=req.product_name, quantity=req.quantity, unit='кг'))
            db.session.commit()

        elif 'name' in request.form:
            db.session.add(MenuItem(
                name=request.form.get('name'),
                price=float(request.form.get('price')),
                description=request.form.get('desc'),
                category=request.form.get('category'),
                ingredients=request.form.get('ingredients')
            ))
            db.session.commit()
            flash('Блюдо добавлено')

    all_orders = Order.query.all()
    requests = PurchaseRequest.query.all()
    income = sum(o.price_paid for o in all_orders)
    expenses = sum(r.cost for r in requests if r.status == 'Одобрено')

    visitors_data = []
    seen = set()
    todays_orders = [o for o in all_orders if o.date.date() == datetime.now().date()]

    for o in todays_orders:
        if o.user_id not in seen:
            type_str = 'Покупка Абон.' if o.status == 'Абонемент' else ('Абонемент' if o.price_paid == 0 else 'Оплата')
            visitors_data.append({'username': o.user.username, 'time': o.date.strftime('%H:%M'), 'type': type_str,
                                  'spent': o.price_paid})
            seen.add(o.user_id)
        elif o.status == 'Абонемент':
            visitors_data.append(
                {'username': o.user.username, 'time': o.date.strftime('%H:%M'), 'type': 'Покупка Абон.',
                 'spent': o.price_paid})

    staff_members = User.query.filter(User.role.in_(['cook', 'admin'])).all()

    return render_template('admin.html', menu=MenuItem.query.all(), requests=requests, income=income, expenses=expenses,
                           profit=income - expenses, visitors_data=visitors_data, staff=staff_members)


@app.route('/delete_item/<int:id>', methods=['POST'])
@login_required
def delete_item(id):
    if current_user.role == 'admin':
        db.session.delete(MenuItem.query.get(id))
        db.session.commit()
    return redirect(url_for('admin_dashboard'))


if __name__ == '__main__':
    # Удаляем старую БД при перезапуске, чтобы применилась новая структура
    if os.path.exists('instance/canteen.db'):
        os.remove('instance/canteen.db')
    elif os.path.exists('canteen.db'):
        os.remove('canteen.db')

    with app.app_context():
        db.create_all()
        # Создаем пользователей
        admin = User(username='admin', password='123', role='admin')
        cook = User(username='cook', password='123', role='cook')
        student = User(username='student', password='123', role='student', balance=4000)

        # Создаем базовые аллергии
        a1 = Allergy(name='мед')
        a2 = Allergy(name='орехи')
        db.session.add_all([admin, cook, student, a1, a2])
        db.session.commit()

        # Присваиваем студенту аллергии (теперь через список объектов)
        student.allergies_list.append(a1)
        student.allergies_list.append(a2)

        # Меню
        db.session.add(MenuItem(name='Омлет', price=80, description='Пышный', category='breakfast',
                                ingredients='яйца, молоко, соль'))
        db.session.add(MenuItem(name='Сырники с медом', price=90, description='Сладкие', category='breakfast',
                                ingredients='творог, мука, яйца, мед'))
        db.session.add(MenuItem(name='Борщ', price=100, description='Классический', category='lunch',
                                ingredients='свёкла, капуста, мясо, лук, картофель'))

        db.session.commit()
    app.run(debug=True, port=5001)