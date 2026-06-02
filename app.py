from flask import Flask, render_template, request, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
import os
import random
import string
import io
import pandas as pd
from collections import Counter
import random
import time

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
# [结构变更] 升级为 v20 数据库，彻底删除多余的部件属性字段，保持极致精简
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'gt_cnc_v21.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ================= 1. 核心数据模型 =================
class DictMachine(db.Model):
    __tablename__ = 'dict_machines'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    code = db.Column(db.String(10), nullable=False)

class DictProductLine(db.Model):
    __tablename__ = 'dict_product_lines'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

class DictSystem(db.Model):
    __tablename__ = 'dict_systems'
    id = db.Column(db.Integer, primary_key=True)
    manufacturer = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(50), unique=True, nullable=False)
    code = db.Column(db.String(10), nullable=False)
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)  
    role = db.Column(db.String(50), nullable=False)  
    product_line = db.Column(db.String(50), default='无') 
    created_at = db.Column(db.DateTime, default=datetime.now)

class Component(db.Model):
    __tablename__ = 'components'
    id = db.Column(db.Integer, primary_key=True)
    
    # [极简重构] 数据库现在有且仅有这三个核心业务属性
    manufacturer = db.Column(db.String(100), nullable=False) 
    name = db.Column(db.String(100), nullable=False)       
    part_no = db.Column(db.String(100), unique=True, nullable=False) 
    
    status = db.Column(db.String(20), default='待审核')
    created_at = db.Column(db.DateTime, default=datetime.now)

class StandardTemplate(db.Model):
    __tablename__ = 'standard_templates'
    id = db.Column(db.Integer, primary_key=True)
    template_no = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), unique=False, nullable=False)
    applicable_machine = db.Column(db.String(50), default='')
    # 存储部件字典列表：[{"part_no": "P001", "is_baseline": True, "node_letter": "A", "node_desc": "主柜"}, ...]
    components_json = db.Column(db.Text, nullable=False) 
    created_at = db.Column(db.DateTime, default=datetime.now)

class CncOrder(db.Model):
    __tablename__ = 'cnc_orders'
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(50), unique=True, nullable=False)
    manufacturer = db.Column(db.String(50))
    series = db.Column(db.String(50))
    applicable_machine = db.Column(db.String(50))
    applicant = db.Column(db.String(50))
    supervisor = db.Column(db.String(50)) 
    team_leader = db.Column(db.String(50)) 
    chief = db.Column(db.String(50)) 
    status = db.Column(db.String(20), default='待主管审批')
    reject_reason = db.Column(db.String(255))  
    approve_remark = db.Column(db.String(255))  
    components_json = db.Column(db.Text, default='[]') 
    matched_template = db.Column(db.String(100), default='无匹配基准')
    match_score = db.Column(db.Float, default=0.0)
    match_adds = db.Column(db.Text, default='[]')
    match_rms = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.now)

class BugFeedback(db.Model):
    __tablename__ = 'bug_feedbacks'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='待处理')
    created_at = db.Column(db.DateTime, default=datetime.now)


# ================= 2. 核心算法与编码引擎 =================
def calculate_multiset_score(list_a, list_b):
    if not list_a and not list_b: return 1.0
    if not list_a or not list_b: return 0.0
    count_a, count_b = Counter(list_a), Counter(list_b)
    intersection = sum((count_a & count_b).values())
    union = sum((count_a | count_b).values())
    return intersection / union if union > 0 else 0
def generate_business_no(order_type, sys_series_text, machine_type):
    # 1. 动态查询机床代码
    mac_obj = DictMachine.query.filter_by(name=machine_type).first()
    m_code = mac_obj.code if mac_obj else "XX"

    # 2. 动态查询系统代码 (兼容前端传过来的是名称或代码)
    sys_obj = DictSystem.query.filter((DictSystem.code == str(sys_series_text)) | (DictSystem.name == str(sys_series_text))).first()
    sys_code = sys_obj.code if sys_obj else "Z0"
    
    # 3. 规则拼接: [机床][类型][系统][年份][3位随机数]
    year_str = datetime.now().strftime("%Y")
    prefix = f"{m_code}{order_type}{sys_code}{year_str}"
    
    random_serial = f"{random.randint(100, 999)}"
    final_no = f"{prefix}{random_serial}"
    
    while CncOrder.query.filter_by(order_no=final_no).first() or \
          StandardTemplate.query.filter_by(template_no=final_no).first():
        random_serial = f"{random.randint(100, 999)}"
        final_no = f"{prefix}{random_serial}"
        
    return final_no


# ================= 3. API 接口 =================
@app.route('/')
def index(): return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(username=data.get('username'), password=data.get('password')).first()
    if user:
        return jsonify({"status": "success", "username": user.username, "role": user.role, "product_line": user.product_line})
    return jsonify({"status": "error", "message": "用户名或密码错误，请重试"})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    u = request.args.get('username', '')
    r = request.args.get('role', '')

    if r == '研发工程师':
        orders = CncOrder.query.filter_by(applicant=u).count()
        pending = CncOrder.query.filter_by(applicant=u).filter(CncOrder.status != '已完成').count()
    elif r == '产品主管':
        orders = CncOrder.query.filter_by(supervisor=u).count()
        pending = CncOrder.query.filter_by(supervisor=u, status='待主管审批').count()
    elif r == '团队负责人':
        orders = CncOrder.query.filter_by(team_leader=u).count()
        pending = CncOrder.query.filter_by(team_leader=u, status='待负责人审批').count()
    elif r == '产品线总师':
        orders = CncOrder.query.filter_by(chief=u).count()
        pending = CncOrder.query.filter_by(chief=u, status='待总师审批').count()
    elif r == '标准管理员':
        # 【修改】标准管理员等同于超管，看所有单子，待办审批也看专属的
        orders = CncOrder.query.count()
        pending = CncOrder.query.filter(CncOrder.status.in_(['待标准审批', '待定基准'])).count()
    else: 
        orders = CncOrder.query.count()
        pending = CncOrder.query.filter(~CncOrder.status.in_(['已完成', '已驳回'])).count()

    return jsonify({
        "total": Component.query.count(), "published": Component.query.filter_by(status='已发布').count(),
        "orders": orders, "templates": StandardTemplate.query.count(), "pending": pending
    })
@app.route('/api/dictionaries', methods=['GET', 'POST', 'DELETE'])
def handle_dictionaries():
    if request.method == 'GET':
        return jsonify({
            "machines": [{"id": m.id, "name": m.name, "code": m.code} for m in DictMachine.query.all()],
            "plines": [{"id": p.id, "name": p.name} for p in DictProductLine.query.all()],
            "systems": [{"id": s.id, "man": s.manufacturer, "name": s.name, "code": s.code} for s in DictSystem.query.all()]
        })

    if request.method == 'POST':
        dtype = request.args.get('type')
        data = request.json
        try:
            if dtype == 'machine': db.session.add(DictMachine(name=data['name'], code=data['code']))
            elif dtype == 'pline': db.session.add(DictProductLine(name=data['name']))
            elif dtype == 'system': db.session.add(DictSystem(manufacturer=data['man'], name=data['name'], code=data['code']))
            db.session.commit()
            return jsonify({"status": "success", "message": "添加成功！"})
        except Exception as e:
            return jsonify({"status": "error", "message": "添加失败，可能存在重复名称！"})

    if request.method == 'DELETE':
        dtype = request.args.get('type')
        req_id = request.args.get('id')
        obj = None
        if dtype == 'machine': obj = db.session.get(DictMachine, req_id)
        elif dtype == 'pline': obj = db.session.get(DictProductLine, req_id)
        elif dtype == 'system': obj = db.session.get(DictSystem, req_id)
        
        if obj: db.session.delete(obj); db.session.commit(); return jsonify({"status": "success"})
        return jsonify({"status": "error"})
@app.route('/api/sidebar/pending', methods=['GET'])
def sidebar_pending_orders():
    username, role = request.args.get('username'), request.args.get('role')
    if not username or not role: return jsonify([])
    
    query = CncOrder.query
    if role == "产品主管": query = query.filter_by(supervisor=username, status="待主管审批")
    elif role == "团队负责人": query = query.filter_by(team_leader=username, status="待负责人审批")
    elif role == "产品线总师": query = query.filter_by(chief=username, status="待总师审批")
    elif role == "标准管理员": query = query.filter(CncOrder.status.in_(['待标准审批', '待定基准']))
    else: return jsonify([])
    
    orders = query.order_by(CncOrder.created_at.desc()).all()
    return jsonify([{"id": o.id, "order_no": o.order_no, "applicant": o.applicant,
                     "manufacturer": o.manufacturer, "status": o.status,
                     "created_at": o.created_at.strftime("%m-%d %H:%M")} for o in orders])

@app.route('/api/users', methods=['GET', 'POST'])
def handle_users():
    if request.method == 'GET':
        users = User.query.order_by(User.created_at.asc()).all()
        return jsonify([{"id": u.id, "username": u.username, "role": u.role, "product_line": u.product_line} for u in users])
    
    if request.method == 'POST':
        req_id = request.json.get('id')
        user = User.query.filter_by(id=req_id).first()
        
        if user:
            user.role = request.json.get('role')
            if 'product_line' in request.json: 
                user.product_line = request.json.get('product_line')
            db.session.commit()
            return jsonify({"status": "success"})
            
        return jsonify({"status": "error", "message": "无法找到该用户，更新失败"})

@app.route('/api/orders/workflow', methods=['POST'])
def order_workflow():
    data = request.json
    order = db.session.get(CncOrder, data.get('id'))
    action = data.get('action')

    if not order: return jsonify({"status": "error", "message": "订单不存在"})

    if action == 'approve':
        # 【核心修改】标准管理员审批通过后 -> 流转到主管审批
        if order.status == '待标准审批':
            order.status = '待主管审批'
            if data.get('remark'):
                order.approve_remark = data.get('remark', '')
        elif order.status == '待主管审批':
            order.status = '待负责人审批'
        elif order.status == '待负责人审批':
            order.status = '待总师审批'
        elif order.status == '待总师审批':
            order.status = '已完成'

    elif action == 'reject':
        order.status = '已驳回'; order.reject_reason = data.get('reason', '系统驳回'); order.approve_remark = ''
    elif action == 'complete':
        order.status = '已完成'
    elif action == 'skip_baseline':
        # 溯源建单人角色
        app_user = User.query.filter_by(username=order.applicant).first()
        app_role = app_user.role if app_user else ''
        if app_role == '团队负责人': order.status = '待总师审批'
        elif app_role == '产品主管': order.status = '待负责人审批'
        else: order.status = '待主管审批'
        
        order.approve_remark = "[标准管理员特批流转] " + data.get('remark', '')
        order.reject_reason = ''
        
    elif action == 'set_baseline':
        # 溯源建单人角色
        app_user = User.query.filter_by(username=order.applicant).first()
        app_role = app_user.role if app_user else ''
        if app_role == '团队负责人': order.status = '待总师审批'
        elif app_role == '产品主管': order.status = '待负责人审批'
        else: order.status = '待主管审批'
        
        order.approve_remark = "[标准管理员已确立新基准] " + data.get('remark', '')
        order.reject_reason = ''
        
        raw_data = json.loads(order.components_json)
        tpl_comps = []
        for p in raw_data:
            if isinstance(p, dict):
                comp_copy = p.copy()
                comp_copy['is_baseline'] = True
                tpl_comps.append(comp_copy)
            else:
                tpl_comps.append({"part_no": p, "is_baseline": True})
        
        json_val = json.dumps(tpl_comps) 
        tpl_name = order.series
        ##new_tpl_no = generate_business_no('ST', order.series, order.applicable_machine)
        new_tpl_no = order.order_no
        new_tpl = StandardTemplate(
            template_no=new_tpl_no, name=tpl_name,
            applicable_machine=order.applicable_machine,
            components_json=json_val 
        )
        
        db.session.add(new_tpl)
        db.session.flush() 
        
        order.matched_template = new_tpl_no
        order.match_score = 1.0
        order.match_adds = '[]'
        order.match_rms = '[]'

    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/api/approvers', methods=['GET'])
def get_approvers():
    pline = request.args.get('product_line', '通用')
    supervisors = User.query.filter_by(role='产品主管', product_line=pline).all()
    leaders = User.query.filter_by(role='团队负责人', product_line=pline).all()
    return jsonify({"supervisors": [u.username for u in supervisors], "leaders": [u.username for u in leaders]})

@app.route('/api/feedback', methods=['GET', 'POST', 'PUT'])
def handle_feedback():
    if request.method == 'GET':
        # 加上 .strip() 清除可能导致判断失效的隐藏空格
        u = request.args.get('username', '').strip()
        r = request.args.get('role', '').strip()
        
        # 【核心修复】同时放行超级管理员和标准管理员
        if r in ['超级管理员', '标准管理员']: 
            feedbacks = BugFeedback.query.order_by(BugFeedback.created_at.desc()).all()
        else: 
            feedbacks = BugFeedback.query.filter_by(username=u).order_by(BugFeedback.created_at.desc()).all()
            
        return jsonify([{"id": f.id, "username": f.username, "title": f.title, "content": f.content, "status": f.status, "created_at": f.created_at.strftime("%Y-%m-%d %H:%M")} for f in feedbacks])
        
    if request.method == 'POST':
        db.session.add(BugFeedback(username=request.json.get('username', '未知'), title=request.json.get('title', ''), content=request.json.get('content', '')))
        db.session.commit()
        return jsonify({"status": "success"})
        
    if request.method == 'PUT':
        f = db.session.get(BugFeedback, request.json.get('id'))
        if f: f.status = '已解决'; db.session.commit(); return jsonify({"status": "success"})
        return jsonify({"status": "error"})

@app.route('/api/utils/parse_excel', methods=['POST'])
def parse_excel():
    if 'file' not in request.files: return jsonify({"status": "error", "message": "未接收到文件"})
    try:
        df = pd.read_excel(request.files['file'], engine='openpyxl')
        parsed_data = []
        missing_parts = [] 
        
        for r in df.to_dict('records'):
            part_no = str(r.get('部件编号', str(r.get('型号', '')))).strip()
            if not part_no or part_no == 'nan': continue
            
            if not Component.query.filter_by(part_no=part_no).first():
                missing_parts.append(part_no)
                continue
            
            raw_qty = r.get('数量', 1)
            try: qty = int(raw_qty)
            except: qty = 1
            
            is_b_str = str(r.get('基准必选', '否')).strip()
            is_baseline = is_b_str == '是' or is_b_str.lower() == 'true' or is_b_str == '1'
            
            parsed_data.append({"part_no": part_no, "qty": qty, "is_baseline": is_baseline})
        
        msg = f"✅ 成功解析并提取了 {len(parsed_data)} 条有效部件！"
        if missing_parts:
            msg += f"\n\n⚠️ 以下部件因【未入库】已被自动跳过：\n{', '.join(set(missing_parts))}"

        return jsonify({"status": "success", "data": parsed_data, "message": msg})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/components/upload', methods=['POST'])
def upload_components():
    if 'file' not in request.files: return jsonify({"status": "error", "message": "未接收到文件"})
    try:
        df = pd.read_excel(request.files['file'], engine='openpyxl')
        s_count, skip_count = 0, 0
        for r in df.to_dict('records'):
            part_no = str(r.get('部件编号', str(r.get('型号', '')))).strip()
            if not part_no or part_no == 'nan': continue
            
            if not Component.query.filter_by(part_no=part_no).first():
                db.session.add(Component(
                    manufacturer=str(r.get('制造商', '未知')).strip(),
                    name=str(r.get('部件名称', '未命名')).strip(),
                    part_no=part_no, 
                    status='已发布'
                ))
                s_count += 1
            else:
                skip_count += 1
        db.session.commit()
        return jsonify({"status": "success", "message": f"导入完成！成功入库 {s_count} 条，跳过已存在 {skip_count} 条。"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"解析失败: {str(e)}"})

@app.route('/api/components', methods=['GET', 'POST', 'DELETE'])
def handle_components():
    if request.method == 'GET':
        query = Component.query
        if request.args.get('status'): query = query.filter_by(status=request.args.get('status'))
        if request.args.get('manufacturer'): query = query.filter(Component.manufacturer.like(f"%{request.args.get('manufacturer')}%"))
        if request.args.get('name'): query = query.filter(Component.name.like(f"%{request.args.get('name')}%"))
        if request.args.get('part_no'): query = query.filter(Component.part_no.like(f"%{request.args.get('part_no')}%"))
        
        comps = query.order_by(Component.created_at.desc()).all()
        return jsonify([{"id": c.id, "manufacturer": c.manufacturer, "name": c.name, "part_no": c.part_no, "status": c.status} for c in comps])

    if request.method == 'POST':
        part_no = request.json.get('part_no', '').strip()
        if not part_no: return jsonify({"status": "error", "message": "部件编号不可为空"})
        if Component.query.filter_by(part_no=part_no).first(): return jsonify({"status": "error", "message": "该部件编号已存在，请勿重复录入"})
        
        db.session.add(Component(
            manufacturer=request.json.get('manufacturer', '未知').strip(), 
            name=request.json.get('name', '未命名部件').strip(),
            part_no=part_no, status='待审核'
        ))
        db.session.commit()
        return jsonify({"status": "success", "message": "部件已提交申请"})

    if request.method == 'DELETE':
        comp = db.session.get(Component, request.args.get('id'))
        if comp: db.session.delete(comp); db.session.commit(); return jsonify({"status": "success", "message": "部件已永久删除"})
        return jsonify({"status": "error", "message": "部件不存在"})

@app.route('/api/components/audit', methods=['POST'])
def audit_component():
    comp = db.session.get(Component, request.json.get('id'))
    if comp: 
        action = request.json.get('action')
        comp.status = action
        if action == '已驳回':
            comp.reject_reason = request.json.get('reason', '')
        db.session.commit() 
        return jsonify({"status": "success"})
    return jsonify({"status": "error"})


@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
def handle_templates():
    if request.method == 'GET':
        return jsonify([{"id": t.id, "no": t.template_no, "name": t.name, "applicable_machine": t.applicable_machine, "data": json.loads(t.components_json)} for t in StandardTemplate.query.order_by(StandardTemplate.created_at.desc()).all()])
    
    if request.method == 'POST':
        req_id = request.json.get('id')
        req_no = request.json.get('no', '').strip()
        req_name = request.json.get('name') 
        req_machine = request.json.get('applicable_machine', '')
        req_components = request.json.get('components', [])
        
        part_nos = [c.get('part_no') for c in req_components if isinstance(c, dict) and c.get('part_no')]
        missing_parts = [p for p in part_nos if not Component.query.filter_by(part_no=p).first()]
        if missing_parts:
            return jsonify({"status": "error", "message": f"以下部件系统库中不存在，请先入库：{', '.join(missing_parts)}"})
        
        if not req_no: req_no = generate_business_no('ST', req_name, req_machine)
        existing_no = StandardTemplate.query.filter_by(template_no=req_no).first()
        if existing_no and str(existing_no.id) != str(req_id): return jsonify({"status": "error", "message": "该基准单号已存在！"})

        if req_id:
            tpl = db.session.get(StandardTemplate, req_id)
            if tpl:
                tpl.template_no, tpl.name, tpl.applicable_machine = req_no, req_name, req_machine
                tpl.components_json = json.dumps(req_components)
                db.session.commit(); return jsonify({"status": "success", "message": "标准基准单修改成功！"})
            return jsonify({"status": "error", "message": "找不到该基准单"})
        else:
            db.session.add(StandardTemplate(
                template_no=req_no, name=req_name, applicable_machine=req_machine, 
                components_json=json.dumps(req_components)
            ))
            db.session.commit(); return jsonify({"status": "success", "message": "标准基准单创建成功"})

    if request.method == 'DELETE':
        tpl = db.session.get(StandardTemplate, request.args.get('id'))
        if tpl: db.session.delete(tpl); db.session.commit()
        return jsonify({"status": "success"})


@app.route('/api/orders/<int:order_id>/export', methods=['GET'])
def export_order(order_id):
    order = db.session.get(CncOrder, order_id)
    if not order: return "订单不存在", 404
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame([{"内部订单号": order.order_no, "设备制造商": order.manufacturer, "系统系列": order.series, "适用机床": order.applicable_machine, "建单时间": order.created_at.strftime("%Y-%m-%d %H:%M")}]).to_excel(writer, sheet_name="1-订单概览", index=False)
        comps = json.loads(order.components_json) 
        if comps:
            enriched_comps = []
            for item in comps:
                p_no = item.get('part_no') if isinstance(item, dict) else str(item)
                node_str = f"[{item.get('node_letter')}] {item.get('node_desc','')}" if isinstance(item, dict) and item.get('node_letter') else "-"
                
                db_comp = Component.query.filter_by(part_no=p_no).first()
                enriched_comps.append({
                    "装配节点": node_str,
                    "制造商": db_comp.manufacturer if db_comp else "-",
                    "部件名称": db_comp.name if db_comp else "-",
                    "部件编号": p_no
                })
            pd.DataFrame(enriched_comps).to_excel(writer, sheet_name="2-BOM硬件清单", index=False)
        else:
            pd.DataFrame([{"装配节点": "-", "制造商": "-", "部件名称": "未录入", "部件编号": "-"}]).to_excel(writer, sheet_name="2-BOM硬件清单", index=False)
            
        adds, rms = json.loads(order.match_adds), json.loads(order.match_rms)
        max_len = max(len(adds), len(rms))
        if max_len > 0:
            adds.extend([''] * (max_len - len(adds))); rms.extend([''] * (max_len - len(rms)))
            pd.DataFrame({f"对比基准: 【{order.matched_template}】": [""] * max_len, "➕ 需新增配置": adds, "➖ 需剔除冗余": rms}).to_excel(writer, sheet_name="3-智能差异分析", index=False)
        else:
            pd.DataFrame([{"提示": "与基准完全一致，无差异"}]).to_excel(writer, sheet_name="3-智能差异分析", index=False)
    output.seek(0)
    from urllib.parse import quote
    return send_file(output, download_name=quote(f"BOM单_{order.order_no}.xlsx"), as_attachment=True, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/api/orders', methods=['GET', 'POST', 'DELETE'])
def handle_orders():
    if request.method == 'GET':
        u, r, search = request.args.get('username', ''), request.args.get('role', ''), request.args.get('search', '').strip()
        query = CncOrder.query
        
        # 【修改】标准管理员等同于超管权限，不设置filter，可看全部
        if r == '研发工程师': query = query.filter_by(applicant=u)
        elif r == '产品主管': query = query.filter_by(supervisor=u)
        elif r == '团队负责人': query = query.filter_by(team_leader=u)
        elif r == '产品线总师': query = query.filter_by(chief=u)

        if search: query = query.filter(db.or_(CncOrder.order_no.like(f"%{search}%"), CncOrder.manufacturer.like(f"%{search}%"), CncOrder.applicable_machine.like(f"%{search}%")))
        orders = query.order_by(CncOrder.created_at.desc()).all()
        return jsonify([{
            "id": o.id, "order_no": o.order_no, "manufacturer": o.manufacturer, "series": o.series,
            "applicable_machine": o.applicable_machine, "status": o.status, "reject_reason": o.reject_reason, "approve_remark": o.approve_remark,
            "components": json.loads(o.components_json), "matched_template": o.matched_template, "match_score": o.match_score,
            "match_adds": json.loads(o.match_adds), "match_rms": json.loads(o.match_rms),
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"), "applicant": o.applicant, "supervisor": o.supervisor, "team_leader": o.team_leader, "chief": o.chief
        } for o in orders])

    if request.method == 'POST':
        data = request.json
        input_comps = data.get('components', []) 
        req_id = data.get('id') # 获取可能传过来的订单 ID
        
        new_all_parts = [] 
        for p in input_comps:
            if isinstance(p, dict):
                part_val = str(p.get('part_no', '')).strip()
            else:
                part_val = str(p).strip()
            if part_val:
                new_all_parts.append(part_val)
        
        missing_parts = [p for p in new_all_parts if not Component.query.filter_by(part_no=p).first()]
        if missing_parts:
            return jsonify({"status": "error", "message": f"以下部件系统库中不存在，请先入库：{', '.join(missing_parts)}"})

        new_counter = Counter(new_all_parts) 
        req_machine = data.get('applicable_machine', '-')
        
        # 1. 尝试完全一致的历史订单（捷径）
        perfect_match = CncOrder.query.filter_by(status='已完成', applicable_machine=req_machine).all()
        for o in perfect_match:
            try:
                o_comps = json.loads(o.components_json)
                o_parts = [c['part_no'] if isinstance(c, dict) else c for c in o_comps]
                if Counter(o_parts) == new_counter:
                    if req_id: # 修改单子并直接完美匹配
                        order = db.session.get(CncOrder, req_id)
                        order.status = '已完成'
                        order.components_json = json.dumps(input_comps)
                        order.matched_template = f"自动复用完全一致订单: {o.order_no}"
                        order.match_score = 1.0
                        order.match_adds = '[]'
                        order.match_rms = '[]'
                        order.reject_reason = ''
                        order.approve_remark = "修改后侦测到完全一致的历史订单配置，已自动为您核准通过。"
                        db.session.commit()
                        return jsonify({"status": "success", "order_no": order.order_no})
                    else: # 新建单子完美匹配
                        final_order_no = generate_business_no('EX', data.get('series', ''), req_machine)
                        chief_u = User.query.filter_by(role='产品线总师', product_line=data.get('product_line', '通用')).first()
                        db.session.add(CncOrder(
                            order_no=final_order_no, manufacturer=data.get('manufacturer', '未知'), series=data.get('series', '-'),
                            applicable_machine=req_machine, applicant=data.get('applicant', '未知'),
                            supervisor=data.get('supervisor'), team_leader=data.get('team_leader'), chief=chief_u.username if chief_u else '未分配',
                            status='已完成', components_json=json.dumps(input_comps), 
                            matched_template=f"自动复用完全一致订单: {o.order_no}",
                            match_score=1.0, match_adds='[]', match_rms='[]',
                            approve_remark="系统侦测到完全一致的历史订单配置，已自动为您核准通过。"
                        ))
                        db.session.commit()
                        return jsonify({"status": "success", "order_no": final_order_no})
            except Exception:
                continue

        # 2. 正常走基准匹配逻辑
        best_tpl, max_score, min_diff_count = None, -1.0, 999999 
        best_adds, best_rms = [], []

        for tpl in StandardTemplate.query.filter_by(applicable_machine=req_machine).all():
            try: tpl_data = json.loads(tpl.components_json) 
            except Exception: continue
            if not isinstance(tpl_data, list): continue

            tpl_all_parts = []
            tpl_baseline_parts = []
            for item in tpl_data:
                if isinstance(item, dict):
                    p = item.get('part_no')
                    if p:
                        tpl_all_parts.append(p)
                        if item.get('is_baseline'): tpl_baseline_parts.append(p)
                else:
                    tpl_all_parts.append(str(item))

            baseline_counter = Counter(tpl_baseline_parts)
            is_valid_match = True
            for p_no, req_count in baseline_counter.items():
                if new_counter.get(p_no, 0) < req_count:
                    is_valid_match = False
                    break
            
            if not is_valid_match: continue

            score = calculate_multiset_score(new_all_parts, tpl_all_parts)
            count_new, count_tpl = Counter(new_all_parts), Counter(tpl_all_parts)
            diff_count = sum((count_new - count_tpl).values()) + sum((count_tpl - count_new).values())

            if score > max_score or (score == max_score and score >= 0 and diff_count < min_diff_count):
                max_score, min_diff_count, best_tpl = score, diff_count, tpl
                best_adds = [f"{k} (×{v})" if v > 1 else k for k, v in (count_new - count_tpl).items()]
                best_rms = [f"{k} (×{v})" if v > 1 else k for k, v in (count_tpl - count_new).items()]

        # 先查出建单人的系统角色
        app_user = User.query.filter_by(username=data.get('applicant', '')).first()
        app_role = app_user.role if app_user else ''

        if best_tpl and max_score >= 0.000001:
            o_type = 'EX'
            # 👇 【越级提报逻辑】：根据建单人角色跳过对应的审批节点
            if app_role == '团队负责人':
                initial_status = '待总师审批'
            elif app_role == '产品主管':
                initial_status = '待负责人审批'
            else:
                initial_status = '待主管审批'
            
            m_template = best_tpl.template_no
        else:
            o_type = 'SP'
            initial_status = '待定基准'  
            m_template = "无匹配基准"
            max_score = 0.0
            best_adds = []
            best_rms = []

        chief_u = User.query.filter_by(role='产品线总师', product_line=data.get('product_line', '通用')).first()
        chief_name = chief_u.username if chief_u else '未分配'

        # 3. 结果保存（分支：修改重提 vs 全新创建）
        if req_id:
            order = db.session.get(CncOrder, req_id)
            order.manufacturer = data.get('manufacturer', '未知')
            order.series = data.get('series', '-')
            order.applicable_machine = req_machine
            order.supervisor = data.get('supervisor')
            order.team_leader = data.get('team_leader')
            order.chief = chief_name
            order.status = initial_status
            order.components_json = json.dumps(input_comps)
            order.matched_template = m_template
            order.match_score = max_score
            order.match_adds = json.dumps(best_adds)
            order.match_rms = json.dumps(best_rms)
            order.reject_reason = ''  # 【核心】：重提后清空原来的驳回原因
            order.approve_remark = ''
            db.session.commit()
            return jsonify({"status": "success", "order_no": order.order_no})
        else:
            final_order_no = generate_business_no(o_type, data.get('series', ''), req_machine)
            db.session.add(CncOrder(
                order_no=final_order_no, manufacturer=data.get('manufacturer', '未知'), series=data.get('series', '-'),
                applicable_machine=req_machine, applicant=data.get('applicant', '未知'),
                supervisor=data.get('supervisor'), team_leader=data.get('team_leader'), chief=chief_name,
                status=initial_status, components_json=json.dumps(input_comps), 
                matched_template=m_template, match_score=max_score, match_adds=json.dumps(best_adds), match_rms=json.dumps(best_rms)
            ))
            db.session.commit()
            return jsonify({"status": "success", "order_no": final_order_no})

    if request.method == 'DELETE':
        order = db.session.get(CncOrder, request.args.get('id'))
        if order: db.session.delete(order); db.session.commit(); return jsonify({"status": "success"})
        return jsonify({"status": "error"})


def init_database():
    db.create_all()
    if User.query.count() == 0:
        db.session.bulk_save_objects([
            # 【修改】把超管也变成标准管理员角色
            User(username="admin", password="123456", role="标准管理员", product_line="全部"),
            User(username="wang", password="1", role="标准管理员", product_line="通用"),
            User(username="chen", password="1", role="研发工程师", product_line="立铣"),
            User(username="li", password="1", role="研发工程师", product_line="立铣"),
            User(username="zhang", password="1", role="产品主管", product_line="立铣"),
            User(username="zhao", password="1", role="团队负责人", product_line="立铣"),
            User(username="qian", password="1", role="产品线总师", product_line="立铣")
        ])
    if Component.query.count() == 0:
        db.session.bulk_save_objects([
            Component(manufacturer="西门子", name="伺服主轴电机", part_no="1FK7", status="已发布"),
            Component(manufacturer="西门子", name="多轴驱动模块", part_no="S120", status="已发布")
        ])
    if StandardTemplate.query.count() == 0:
        db.session.add(StandardTemplate(
            template_no="STA02026001", name="西门子840Dsl", applicable_machine="立式加工中心",
            components_json=json.dumps([
                {"part_no": "1FK7", "is_baseline": True, "node_letter": "A", "node_desc": "主控制柜"},
                {"part_no": "S120", "is_baseline": False, "node_letter": "A", "node_desc": "主控制柜"}
            ])
        ))
    if DictMachine.query.count() == 0:
        db.session.bulk_save_objects([
            DictMachine(name="立式加工中心", code="VC"), DictMachine(name="龙门式铣床", code="GM"),
            DictMachine(name="卧式加工中心", code="HM"), DictMachine(name="落地式镗床", code="FB"), DictMachine(name="刨台式镗床", code="PB")
        ])
    if DictProductLine.query.count() == 0:
        db.session.bulk_save_objects([
            DictProductLine(name="立铣"), DictProductLine(name="卧铣"), DictProductLine(name="镗削"), DictProductLine(name="通用"), DictProductLine(name="全部")
        ])
    if DictSystem.query.count() == 0:
        db.session.bulk_save_objects([
            DictSystem(manufacturer="西门子", name="西门子840Dsl", code="A1"), DictSystem(manufacturer="西门子", name="西门子one", code="A5"),
            DictSystem(manufacturer="发那科", name="发那科31i", code="B4"), DictSystem(manufacturer="发那科", name="发那科0i-MF Plus(3B)", code="BH"),
            DictSystem(manufacturer="发那科", name="发那科0i-MF Plus(3A)", code="BG"), DictSystem(manufacturer="发那科", name="发那科0i-MF Plus(1A)", code="BF")
        ])
    db.session.commit()

if __name__ == '__main__':
    with app.app_context(): init_database()
    app.run(debug=True, host='0.0.0.0', port=6677)