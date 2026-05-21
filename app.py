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

app = Flask(__name__)
basedir = os.path.abspath(os.path.dirname(__file__))
# [结构变更] 升级为 v18 极简数据库，彻底剔除所有冗余字段，仅保留核心三要素
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'gt_cnc_v18.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ================= 1. 核心数据模型 =================
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
    
    # [极简重构] 彻底只保留这三个核心属性
    manufacturer = db.Column(db.String(100), nullable=False) 
    name = db.Column(db.String(100), nullable=False)       
    part_no = db.Column(db.String(100), unique=True, nullable=False) 
    
    status = db.Column(db.String(20), default='待审核')
    created_at = db.Column(db.DateTime, default=datetime.now)

class StandardTemplate(db.Model):
    __tablename__ = 'standard_templates'
    id = db.Column(db.Integer, primary_key=True)
    template_no = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), unique=True, nullable=False)
    components_json = db.Column(db.Text, nullable=False) # 存储纯粹的部件编号列表：["P001", "P002", "P002"]
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
    components_json = db.Column(db.Text, default='[]') # 存储纯粹的部件编号列表：["P001", "P002"]
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
    """极简版匹配算法：直接基于部件编号的交并集计算吻合度"""
    if not list_a and not list_b: return 1.0
    if not list_a or not list_b: return 0.0
    count_a, count_b = Counter(list_a), Counter(list_b)
    intersection = sum((count_a & count_b).values())
    union = sum((count_a | count_b).values())
    return intersection / union if union > 0 else 0

def generate_business_no(order_type, sys_series_text):
    sys_code = 'Z0'  
    series_upper = str(sys_series_text).upper()
    if '828' in series_upper: sys_code = 'A1'
    elif '840' in series_upper: sys_code = 'A0'
    elif 'FANUC' in series_upper or '0I' in series_upper or '31I' in series_upper: sys_code = 'F0'
    elif 'OKUMA' in series_upper or 'OSP' in series_upper: sys_code = 'O0'  
    elif 'MAZAK' in series_upper or 'MAZATROL' in series_upper or 'SMOOTH' in series_upper: sys_code = 'M0'  
    
    year_str = datetime.now().strftime("%Y")
    prefix = f"{order_type}{sys_code}{year_str}"
    count_orders = CncOrder.query.filter(CncOrder.order_no.like(f"{prefix}%")).count()
    count_tpls = StandardTemplate.query.filter(StandardTemplate.template_no.like(f"{prefix}%")).count()
    serial = count_orders + count_tpls + 1
    return f"{prefix}{serial:03d}"


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
        orders = CncOrder.query.filter(CncOrder.status.in_(['待标准审批', '待定基准', '待采购', '已完成'])).count()
        pending = CncOrder.query.filter(CncOrder.status.in_(['待标准审批', '待定基准'])).count()
    elif r == '采购员':
        orders = CncOrder.query.filter_by(status='待采购').count()
        pending = 0
    else: 
        orders = CncOrder.query.count()
        pending = CncOrder.query.filter(~CncOrder.status.in_(['已完成', '已驳回'])).count()

    return jsonify({
        "total": Component.query.count(), "published": Component.query.filter_by(status='已发布').count(),
        "orders": orders, "templates": StandardTemplate.query.count(), "pending": pending
    })

@app.route('/api/sidebar/pending', methods=['GET'])
def sidebar_pending_orders():
    username, role = request.args.get('username'), request.args.get('role')
    if not username or not role: return jsonify([])
    
    query = CncOrder.query
    if role == "产品主管": query = query.filter_by(supervisor=username, status="待主管审批")
    elif role == "团队负责人": query = query.filter_by(team_leader=username, status="待负责人审批")
    elif role == "产品线总师": query = query.filter_by(chief=username, status="待总师审批")
    elif role == "标准管理员": query = query.filter(CncOrder.status.in_(['待标准审批', '待定基准']))
    elif role == "采购员": query = query.filter_by(status="待采购")
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
        user = db.session.get(User, request.json.get('id'))
        if user:
            user.role = request.json.get('role')
            if 'product_line' in request.json: user.product_line = request.json.get('product_line')
            db.session.commit()
            return jsonify({"status": "success"})
        return jsonify({"status": "error"})

@app.route('/api/approvers', methods=['GET'])
def get_approvers():
    pline = request.args.get('product_line', '通用')
    supervisors = User.query.filter_by(role='产品主管', product_line=pline).all()
    leaders = User.query.filter_by(role='团队负责人', product_line=pline).all()
    return jsonify({"supervisors": [u.username for u in supervisors], "leaders": [u.username for u in leaders]})

@app.route('/api/feedback', methods=['GET', 'POST', 'PUT'])
def handle_feedback():
    if request.method == 'GET':
        u, r = request.args.get('username', ''), request.args.get('role', '')
        if r == '超级管理员': feedbacks = BugFeedback.query.order_by(BugFeedback.created_at.desc()).all()
        else: feedbacks = BugFeedback.query.filter_by(username=u).order_by(BugFeedback.created_at.desc()).all()
        return jsonify([{"id": f.id, "username": f.username, "title": f.title, "content": f.content, "status": f.status, "created_at": f.created_at.strftime("%Y-%m-%d %H:%M")} for f in feedbacks])
    if request.method == 'POST':
        db.session.add(BugFeedback(username=request.json.get('username', '未知'), title=request.json.get('title', ''), content=request.json.get('content', '')))
        db.session.commit()
        return jsonify({"status": "success"})
    if request.method == 'PUT':
        f = db.session.get(BugFeedback, request.json.get('id'))
        if f: f.status = '已解决'; db.session.commit(); return jsonify({"status": "success"})
        return jsonify({"status": "error"})

@app.route('/api/orders/workflow', methods=['POST'])
def order_workflow():
    data = request.json
    order = db.session.get(CncOrder, data.get('id'))
    action = data.get('action')
    if not order: return jsonify({"status": "error", "message": "订单不存在"})

    if action == 'approve':
        if order.status == '待主管审批':
            order.status = '待负责人审批' if order.match_score >= 0.1 else '待定基准'
        elif order.status == '待负责人审批': order.status = '待总师审批'
        elif order.status == '待总师审批':
            order.status = '已完成'; order.approve_remark = data.get('remark', '')
            order.reject_reason = ''
    elif action == 'reject':
        order.status = '已驳回'; order.reject_reason = data.get('reason', '系统驳回'); order.approve_remark = ''
    elif action == 'complete':
        order.status = '已完成'
    elif action == 'skip_baseline':
        order.status = '待标准审批'; order.approve_remark = "[超管特批流转] " + data.get('remark', '')
        order.reject_reason = ''
    elif action == 'set_baseline':
        order.status = '待标准审批'; order.approve_remark = "[超管已确立新基准] " + data.get('remark', '')
        order.reject_reason = ''

        order_comps = json.loads(order.components_json) # list of part_nos
        sys_combine_str = f"{order.manufacturer} {order.series}"
        new_tpl_no = generate_business_no('ST', sys_combine_str)
        db.session.add(StandardTemplate(
            template_no=new_tpl_no, name=f"基于 {order.order_no} 生成的基准", components_json=json.dumps(order_comps) 
        ))
        order.matched_template, order.match_score, order.match_adds, order.match_rms = f"新基准: {new_tpl_no}", 1.0, '[]', '[]'

    db.session.commit()
    return jsonify({"status": "success"})

@app.route('/api/components/upload', methods=['POST'])
def upload_components():
    if 'file' not in request.files: return jsonify({"status": "error", "message": "未接收到文件"})
    try:
        df = pd.read_excel(request.files['file'], engine='openpyxl')
        s_count, skip_count = 0, 0
        for r in df.to_dict('records'):
            part_no = str(r.get('部件编号', '')).strip()
            name = str(r.get('部件名称', '')).strip()
            manuf = str(r.get('制造商', '未知')).strip()
            
            if not part_no or part_no == 'nan': continue
            if manuf == 'nan': manuf = '未知'
            if name == 'nan': name = '未命名部件'
            
            if not Component.query.filter_by(part_no=part_no).first():
                db.session.add(Component(manufacturer=manuf, name=name, part_no=part_no, status='已发布'))
                s_count += 1
            else: skip_count += 1
        db.session.commit()
        return jsonify({"status": "success", "message": f"导入完成！成功 {s_count} 条，跳过已存在 {skip_count} 条。"})
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
    if comp: comp.status = request.json.get('action'); db.session.commit(); return jsonify({"status": "success"})
    return jsonify({"status": "error"})

@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
def handle_templates():
    if request.method == 'GET':
        return jsonify([{"id": t.id, "no": t.template_no, "name": t.name, "data": json.loads(t.components_json)} for t in StandardTemplate.query.order_by(StandardTemplate.created_at.desc()).all()])
    if request.method == 'POST':
        req_id = request.json.get('id')
        req_no = request.json.get('no', '').strip()
        req_name = request.json.get('name') 
        
        if not req_no: req_no = generate_business_no('ST', req_name)
        existing_no = StandardTemplate.query.filter_by(template_no=req_no).first()
        if existing_no and str(existing_no.id) != str(req_id): return jsonify({"status": "error", "message": "该基准单号已存在！"})

        if req_id:
            tpl = db.session.get(StandardTemplate, req_id)
            if tpl:
                tpl.template_no, tpl.name, tpl.components_json = req_no, req_name, json.dumps(request.json.get('components'))
                db.session.commit(); return jsonify({"status": "success", "message": "标准基准单修改成功！"})
            return jsonify({"status": "error", "message": "找不到该基准单"})
        else:
            db.session.add(StandardTemplate(template_no=req_no, name=req_name, components_json=json.dumps(request.json.get('components'))))
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
        comps = json.loads(order.components_json) # list of part_nos
        if comps:
            enriched_comps = []
            for p_no in comps:
                db_comp = Component.query.filter_by(part_no=p_no).first()
                enriched_comps.append({
                    "制造商": db_comp.manufacturer if db_comp else "-",
                    "部件名称": db_comp.name if db_comp else "-",
                    "部件编号": p_no
                })
            pd.DataFrame(enriched_comps).to_excel(writer, sheet_name="2-BOM硬件清单", index=False)
        else:
            pd.DataFrame([{"制造商": "-", "部件名称": "未录入", "部件编号": "-"}]).to_excel(writer, sheet_name="2-BOM硬件清单", index=False)
            
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
        u = request.args.get('username', '')
        r = request.args.get('role', '')
        search = request.args.get('search', '').strip()

        query = CncOrder.query
        if r == '研发工程师': query = query.filter_by(applicant=u)
        elif r == '产品主管': query = query.filter_by(supervisor=u)
        elif r == '团队负责人': query = query.filter_by(team_leader=u)
        elif r == '产品线总师': query = query.filter_by(chief=u)
        elif r == '标准管理员': query = query.filter(CncOrder.status.in_(['待标准审批', '待定基准', '待采购', '已完成']))
        elif r == '采购员': query = query.filter_by(status='待采购')

        if search:
            query = query.filter(db.or_(CncOrder.order_no.like(f"%{search}%"), CncOrder.manufacturer.like(f"%{search}%"), CncOrder.applicable_machine.like(f"%{search}%")))

        orders = query.order_by(CncOrder.created_at.desc()).all()
        return jsonify([{
            "id": o.id, "order_no": o.order_no, "manufacturer": o.manufacturer, "series": o.series,
            "applicable_machine": o.applicable_machine, "status": o.status, "reject_reason": o.reject_reason, "approve_remark": o.approve_remark,
            "components": json.loads(o.components_json), "matched_template": o.matched_template,
            "match_score": o.match_score, "match_adds": json.loads(o.match_adds),
            "match_rms": json.loads(o.match_rms), "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            "applicant": o.applicant, "supervisor": o.supervisor, "team_leader": o.team_leader, "chief": o.chief
        } for o in orders])

    if request.method == 'POST':
        data = request.json
        # 前端现在直接传一个编号数组，或者包含part_no的对象数组，统一处理为字符串列表
        input_comps = data.get('components', []) 
        new_all = [str(p).strip() for p in input_comps if str(p).strip()]

        best_tpl, max_score, min_diff_count = None, 0.0, 999999 
        best_adds, best_rms = [], []

        for tpl in StandardTemplate.query.all():
            try: tpl_all = json.loads(tpl.components_json)
            except Exception: continue
            if not isinstance(tpl_all, list): continue

            score = calculate_multiset_score(new_all, tpl_all)
            count_new, count_tpl = Counter(new_all), Counter(tpl_all)
            diff_count = sum((count_new - count_tpl).values()) + sum((count_tpl - count_new).values())

            is_better = False
            if score > max_score: is_better = True
            elif score == max_score and score > 0:
                if diff_count < min_diff_count: is_better = True

            if is_better: 
                max_score = score
                min_diff_count = diff_count
                best_tpl = tpl
                best_adds = [f"{k} (×{v})" if v > 1 else k for k, v in (count_new - count_tpl).items()]
                best_rms = [f"{k} (×{v})" if v > 1 else k for k, v in (count_tpl - count_new).items()]

        o_type = 'EX' if max_score >= 0.1 else 'SP' 
        sys_combine_str = f"{data.get('manufacturer', '')} {data.get('series', '')}"
        final_order_no = generate_business_no(o_type, sys_combine_str)
        matched_name = best_tpl.name if max_score >= 0.1 and best_tpl else "无匹配基准 (配置差异过大)"

        pline = data.get('product_line', '通用')
        chief_u = User.query.filter_by(role='产品线总师', product_line=pline).first()
        chief_name = chief_u.username if chief_u else '未分配总师'

        db.session.add(CncOrder(
            order_no=final_order_no, manufacturer=data.get('manufacturer', '未知'), series=data.get('series', '-'),
            applicable_machine=data.get('applicable_machine', '-'), applicant=data.get('applicant', '未知'),
            supervisor=data.get('supervisor'), team_leader=data.get('team_leader'), chief=chief_name,
            status='待主管审批', components_json=json.dumps(new_all), matched_template=matched_name,
            match_score=max_score, match_adds=json.dumps(best_adds), match_rms=json.dumps(best_rms)
        ))
        db.session.commit()
        return jsonify({"status": "success", "order_no": final_order_no})

    if request.method == 'DELETE':
        order = db.session.get(CncOrder, request.args.get('id'))
        if order: db.session.delete(order); db.session.commit(); return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "订单不存在"})


def init_database():
    db.create_all()
    if User.query.count() == 0:
        db.session.bulk_save_objects([
            User(username="admin", password="123456", role="超级管理员", product_line="全部"),
            User(username="wang", password="1", role="标准管理员", product_line="通用"),
            User(username="li", password="1", role="采购员", product_line="通用"),
            User(username="chen", password="1", role="研发工程师", product_line="立铣"),
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
        db.session.add(StandardTemplate(template_no="STA02026001", name="西门子840Dsl", 
                                        components_json=json.dumps(["1FK7", "1FK7", "1FK7", "S120"])))
    db.session.commit()


if __name__ == '__main__':
    with app.app_context(): init_database()
    app.run(debug=True, host='0.0.0.0', port=6677)