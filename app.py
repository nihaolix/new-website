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
# [结构变更] 升级为 v13 数据库，支持部件制造商字段
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'gt_cnc_v13.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ================= 全局分类定义 =================
ALL_CATEGORIES = ["电机", "驱动", "操作部件", "电缆", "系统", "服务", "手册", "网络 / 连接配件", "电气安装辅件",
                  "软件 / 功能授权", "其他"]
CORE_CATEGORIES = ["电机", "驱动", "系统"]


# ================= 1. 核心数据模型 =================
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)  
    role = db.Column(db.String(50), nullable=False)  
    created_at = db.Column(db.DateTime, default=datetime.now)

class Component(db.Model):
    __tablename__ = 'components'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(50), nullable=False)
    manufacturer = db.Column(db.String(100), default='未知') 
    model = db.Column(db.String(100), unique=True, nullable=False)
    erp_code = db.Column(db.String(50))
    remark = db.Column(db.String(255))
    status = db.Column(db.String(20), default='待审核')
    created_at = db.Column(db.DateTime, default=datetime.now)

class StandardTemplate(db.Model):
    __tablename__ = 'standard_templates'
    id = db.Column(db.Integer, primary_key=True)
    template_no = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100), unique=True, nullable=False)
    motor_count = db.Column(db.Integer, default=0)
    components_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    erp_code = db.Column(db.String(50), default='')

class CncOrder(db.Model):
    __tablename__ = 'cnc_orders'
    id = db.Column(db.Integer, primary_key=True)
    order_no = db.Column(db.String(50), unique=True, nullable=False)
    order_type = db.Column(db.String(20), default='专用订单')
    manufacturer = db.Column(db.String(50))
    series = db.Column(db.String(50))
    motor_count = db.Column(db.Integer, default=0)
    drive_count = db.Column(db.Integer, default=0)
    applicant = db.Column(db.String(50))
    applicable_machine = db.Column(db.String(50))
    status = db.Column(db.String(20), default='待审批')
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

def generate_short_id():
    return 'N' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=7))


def generate_business_no(order_type, sys_series_text):
    """
    根据图纸规范自动生成单号
    - order_type: 'ST'(标准单/基准), 'EX'(扩展单), 'SP'(专用单)
    """
    # 【修改】：将兜底默认值改为 Z0（未知或其他系统）
    sys_code = 'Z0'  
    
    # 提取并转为大写，方便进行关键字抓取
    series_upper = str(sys_series_text).upper()
    
    # ======= 以后您可以在这里无限添加新规则 =======
    if '828' in series_upper:
        sys_code = 'A1'
    elif '840' in series_upper:
        sys_code = 'A0'
    elif 'FANUC' in series_upper or '0I' in series_upper or '31I' in series_upper:
        sys_code = 'F0'
    elif 'OKUMA' in series_upper or 'OSP' in series_upper:
        sys_code = 'O0'  # 自动识别大隈 OSP 系列
    elif 'MAZAK' in series_upper or 'MAZATROL' in series_upper or 'SMOOTH' in series_upper:
        sys_code = 'M0'  # 自动识别马扎克系列
    # 照葫芦画瓢：
    # elif 'HUAZHONG' in series_upper or 'HNC' in series_upper:
    #     sys_code = 'H0'
    # ============================================
    
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
    u = data.get('username')
    p = data.get('password')
    user = User.query.filter_by(username=u, password=p).first()
    if user:
        return jsonify({"status": "success", "username": user.username, "role": user.role})
    return jsonify({"status": "error", "message": "用户名或密码错误，请重试"})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    u = request.args.get('username', '')
    r = request.args.get('role', '')

    if r == '研发工程师':
        orders = CncOrder.query.filter_by(applicant=u).count()
        pending = CncOrder.query.filter_by(applicant=u, status='待审批').count()
    elif r == '标准管理员':
        orders = CncOrder.query.filter_by(status='待审批').count()
        pending = orders
    elif r == '采购员':
        orders = CncOrder.query.filter_by(status='待采购').count()
        pending = 0
    else: 
        orders = CncOrder.query.count()
        pending = CncOrder.query.filter(CncOrder.status.in_(['待审批', '待定基准'])).count()

    return jsonify({
        "total": Component.query.count(), "published": Component.query.filter_by(status='已发布').count(),
        "orders": orders, "templates": StandardTemplate.query.count(),
        "pending": pending
    })


@app.route('/api/users', methods=['GET', 'POST'])
def handle_users():
    if request.method == 'GET':
        users = User.query.order_by(User.created_at.asc()).all()
        return jsonify([{"id": u.id, "username": u.username, "role": u.role} for u in users])
    if request.method == 'POST':
        user = db.session.get(User, request.json.get('id'))
        if user:
            user.role = request.json.get('role')
            db.session.commit()
            return jsonify({"status": "success"})
        return jsonify({"status": "error"})


@app.route('/api/feedback', methods=['GET', 'POST', 'PUT'])
def handle_feedback():
    if request.method == 'GET':
        u = request.args.get('username', '')
        r = request.args.get('role', '')

        if r == '超级管理员':
            feedbacks = BugFeedback.query.order_by(BugFeedback.created_at.desc()).all()
        else:
            feedbacks = BugFeedback.query.filter_by(username=u).order_by(BugFeedback.created_at.desc()).all()

        return jsonify([{
            "id": f.id, "username": f.username, "title": f.title,
            "content": f.content, "status": f.status,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M")
        } for f in feedbacks])

    if request.method == 'POST':
        data = request.json
        db.session.add(BugFeedback(
            username=data.get('username', '未知'),
            title=data.get('title', ''),
            content=data.get('content', '')
        ))
        db.session.commit()
        return jsonify({"status": "success"})

    if request.method == 'PUT':
        f = db.session.get(BugFeedback, request.json.get('id'))
        if f:
            f.status = '已解决'
            db.session.commit()
            return jsonify({"status": "success"})
        return jsonify({"status": "error"})


@app.route('/api/orders/workflow', methods=['POST'])
def order_workflow():
    data = request.json
    order = db.session.get(CncOrder, data.get('id'))
    action = data.get('action')
    if not order: return jsonify({"status": "error", "message": "订单不存在"})

    if action == 'approve':
        order.status = '待采购'
        order.approve_remark = data.get('remark', '')
        order.reject_reason = ''
    elif action == 'reject':
        order.status = '已驳回'
        order.reject_reason = data.get('reason', '系统驳回')
        order.approve_remark = ''
    elif action == 'complete':
        order.status = '已完成'
    elif action == 'skip_baseline':
        order.status = '待审批'
        order.approve_remark = "[超管特批流转] " + data.get('remark', '')
        order.reject_reason = ''
    elif action == 'set_baseline':
        order.status = '待审批'
        order.approve_remark = "[超管已确立新基准] " + data.get('remark', '')
        order.reject_reason = ''

        order_comps = json.loads(order.components_json)
        tpl_comps = {cat: [] for cat in ALL_CATEGORIES}
        for c in order_comps:
            cat = c.get('category', '其他')
            mod = c.get('model', '').strip()
            if mod:
                if cat not in tpl_comps: tpl_comps[cat] = []
                tpl_comps[cat].append(mod)

        sys_combine_str = f"{order.manufacturer} {order.series}"
        new_tpl_no = generate_business_no('ST', sys_combine_str)
        db.session.add(StandardTemplate(
            template_no=new_tpl_no,
            name=f"基于 {order.order_no} 自动生成的定制基准",
            motor_count=order.motor_count,
            components_json=json.dumps(tpl_comps) 
        ))

        order.matched_template = f"新基准: {new_tpl_no}"
        order.match_score = 1.0
        order.match_adds = '[]'
        order.match_rms = '[]'

    db.session.commit()
    return jsonify({"status": "success"})


@app.route('/api/components/upload', methods=['POST'])
def upload_components():
    if 'file' not in request.files: return jsonify({"status": "error", "message": "未接收到文件"})
    try:
        df = pd.read_excel(request.files['file'], engine='openpyxl')
        success_count, skip_count = 0, 0
        for r in df.to_dict('records'):
            model = str(r.get('型号', '')).strip()
            if not model or model == 'nan': continue
            cat = str(r.get('分类', '其他')).strip()
            manuf = str(r.get('制造商', '未知')).strip()
            if manuf == 'nan': manuf = '未知'
            
            if not Component.query.filter_by(model=model).first():
                db.session.add(Component(category=cat if cat in ALL_CATEGORIES else '其他', 
                                         model=model,
                                         manufacturer=manuf,
                                         erp_code=str(r.get('ERP', '')).strip(), 
                                         remark=str(r.get('备注', '')).strip(),
                                         status='已发布'))
                success_count += 1
            else:
                skip_count += 1
        db.session.commit()
        return jsonify(
            {"status": "success", "message": f"导入完成！成功 {success_count} 条，跳过已存在 {skip_count} 条。"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"解析失败: {str(e)}"})


@app.route('/api/components', methods=['GET', 'POST', 'DELETE'])
def handle_components():
    if request.method == 'GET':
        query = Component.query
        if request.args.get('status'): query = query.filter_by(status=request.args.get('status'))
        if request.args.get('category') and request.args.get('category') != '全部': query = query.filter_by(
            category=request.args.get('category'))
        if request.args.get('manufacturer'): query = query.filter(Component.manufacturer.like(f"%{request.args.get('manufacturer')}%"))
        if request.args.get('model'): query = query.filter(Component.model.like(f"%{request.args.get('model')}%"))
        comps = query.order_by(Component.created_at.desc()).all()
        return jsonify([{"id": c.id, "category": c.category, "manufacturer": c.manufacturer, "model": c.model, "erp": c.erp_code, "remark": c.remark,
                         "status": c.status} for c in comps])

    if request.method == 'POST':
        if Component.query.filter_by(model=request.json.get('model', '').strip()).first(): return jsonify(
            {"status": "error", "message": "型号已存在"})
        db.session.add(
            Component(category=request.json.get('category', '其他'), 
                      manufacturer=request.json.get('manufacturer', '未知').strip(), 
                      model=request.json.get('model', '').strip(),
                      erp_code=request.json.get('erp'), 
                      remark=request.json.get('remark'), status='待审核'))
        db.session.commit()
        return jsonify({"status": "success", "message": "已提交申请"})

    if request.method == 'DELETE':
        comp = db.session.get(Component, request.args.get('id'))
        if comp:
            db.session.delete(comp)
            db.session.commit()
            return jsonify({"status": "success", "message": "部件已永久删除"})
        return jsonify({"status": "error", "message": "部件不存在"})


@app.route('/api/components/audit', methods=['POST'])
def audit_component():
    comp = db.session.get(Component, request.json.get('id'))
    if comp: comp.status = request.json.get('action'); db.session.commit(); return jsonify({"status": "success"})
    return jsonify({"status": "error"})


@app.route('/api/templates', methods=['GET', 'POST', 'DELETE'])
def handle_templates():
    if request.method == 'GET':
        return jsonify([{"id": t.id, "no": t.template_no, "name": t.name, "erp_code": t.erp_code, "motor_count": t.motor_count,
                         "data": json.loads(t.components_json)} for t in
                        StandardTemplate.query.order_by(StandardTemplate.created_at.desc()).all()])
    if request.method == 'POST':
        req_id = request.json.get('id')
        req_no = request.json.get('no', '').strip()
        req_name = request.json.get('name') 
        req_erp = request.json.get('erp_code', '') 

        if not req_no:
            req_no = generate_business_no('ST', req_name)

        existing_no = StandardTemplate.query.filter_by(template_no=req_no).first()
        if existing_no and str(existing_no.id) != str(req_id):
            return jsonify({"status": "error", "message": "该基准单号已存在，请更换一个新的编号！"})

        if req_id:
            tpl = db.session.get(StandardTemplate, req_id)
            if tpl:
                tpl.template_no = req_no
                tpl.name = req_name
                tpl.erp_code = req_erp
                tpl.motor_count = int(request.json.get('motor_count', 0))
                tpl.components_json = json.dumps(request.json.get('components'))
                db.session.commit()
                return jsonify({"status": "success", "message": "标准订单修改成功！"})
            return jsonify({"status": "error", "message": "找不到该基准单"})
        else:
            db.session.add(StandardTemplate(
                template_no=req_no,
                name=req_name,
                erp_code=req_erp, 
                motor_count=int(request.json.get('motor_count', 0)),
                components_json=json.dumps(request.json.get('components'))
            ))
            db.session.commit()
            return jsonify({"status": "success", "message": "标准订单创建/保存成功"})

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
        pd.DataFrame([{"内部订单号": order.order_no, "设备制造商": order.manufacturer, "系统系列": order.series,
                       "电机数量": order.motor_count, "驱动数量": order.drive_count,
                       "适用机床": order.applicable_machine,
                       "建单时间": order.created_at.strftime("%Y-%m-%d %H:%M")}]).to_excel(writer,
                                                                                           sheet_name="1-订单概览",
                                                                                           index=False)
        comps = json.loads(order.components_json)
        if comps:
            enriched_comps = []
            for c in comps:
                db_comp = Component.query.filter_by(model=c['model']).first()
                enriched_comps.append({"部件分类": c['category'], "明细型号": c['model'],
                                       "ERP物料号": db_comp.erp_code if db_comp else "-",
                                       "备注说明": db_comp.remark if db_comp else "-"})
            pd.DataFrame(enriched_comps).to_excel(writer, sheet_name="2-BOM硬件清单", index=False)
        else:
            pd.DataFrame([{"部件分类": "未录入", "明细型号": "未录入", "ERP物料号": "-", "备注说明": "-"}]).to_excel(
                writer, sheet_name="2-BOM硬件清单", index=False)
        adds, rms = json.loads(order.match_adds), json.loads(order.match_rms)
        max_len = max(len(adds), len(rms))
        if max_len > 0:
            adds.extend([''] * (max_len - len(adds)));
            rms.extend([''] * (max_len - len(rms)))
            pd.DataFrame({f"对比基准: 【{order.matched_template}】": [""] * max_len, "➕ 需在基准上新增配置": adds,
                          "➖ 需从基准中剔除冗余": rms}).to_excel(writer, sheet_name="3-智能差异分析", index=False)
        else:
            pd.DataFrame([{"提示": "与基准完全一致，无差异"}]).to_excel(writer, sheet_name="3-智能差异分析", index=False)
    output.seek(0)
    from urllib.parse import quote
    return send_file(output, download_name=quote(f"BOM生产核验单_{order.order_no}.xlsx"), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route('/api/orders', methods=['GET', 'POST', 'DELETE'])
def handle_orders():
    if request.method == 'GET':
        u = request.args.get('username', '')
        r = request.args.get('role', '')
        search = request.args.get('search', '').strip()

        query = CncOrder.query

        if r == '研发工程师':
            query = query.filter_by(applicant=u)
        elif r == '标准管理员':
            query = query.filter_by(status='待审批')
        elif r == '采购员':
            query = query.filter_by(status='待采购')

        if search:
            query = query.filter(db.or_(
                CncOrder.order_no.like(f"%{search}%"),
                CncOrder.manufacturer.like(f"%{search}%"),
                CncOrder.applicable_machine.like(f"%{search}%")
            ))

        orders = query.order_by(CncOrder.created_at.desc()).all()
        return jsonify([{
            "id": o.id, "order_no": o.order_no, "manufacturer": o.manufacturer, "series": o.series,
            "motor_count": o.motor_count, "drive_count": o.drive_count, "applicable_machine": o.applicable_machine,
            "status": o.status, "reject_reason": o.reject_reason, "approve_remark": o.approve_remark,
            "components": json.loads(o.components_json), "matched_template": o.matched_template,
            "match_score": o.match_score, "match_adds": json.loads(o.match_adds),
            "match_rms": json.loads(o.match_rms), "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
            "applicant": o.applicant  
        } for o in orders])

    if request.method == 'POST':
        data = request.json
        input_comps = data.get('components', [])
        
        new_motors, new_others, new_all = [], [], []
        for item in input_comps:
            cat = item.get('category', '其他')
            mod = item.get('model', '').strip()
            if mod:
                new_all.append(mod)
                if cat == '电机':
                    new_motors.append(mod)
                else:
                    new_others.append(mod)

        best_tpl, max_score = None, 0.0
        best_adds, best_rms = [], []

        for tpl in StandardTemplate.query.all():
            try:
                tpl_data = json.loads(tpl.components_json)
            except Exception:
                continue
            
            if not isinstance(tpl_data, dict):
                continue

            tpl_motors, tpl_others, tpl_all = [], [], []
            for cat, models in tpl_data.items():
                tpl_all.extend(models)
                if cat == '电机':
                    tpl_motors.extend(models)
                else:
                    tpl_others.extend(models)

            if new_motors or tpl_motors:
                score = calculate_multiset_score(new_motors, tpl_motors)
            else:
                score = calculate_multiset_score(new_others, tpl_others)

            if score > max_score: 
                max_score = score
                best_tpl = tpl
                
                count_new, count_tpl = Counter(new_all), Counter(tpl_all)
                best_adds = list((count_new - count_tpl).elements()) 
                best_rms = list((count_tpl - count_new).elements())  

        if max_score >= 0.1:
            o_type = 'EX' 
        else:
            o_type = 'SP' 
            
        sys_combine_str = f"{data.get('manufacturer', '')} {data.get('series', '')}"
        final_order_no = generate_business_no(o_type, sys_combine_str)
                
        matched_name = best_tpl.name if max_score >= 0.1 and best_tpl else "无匹配基准 (电机差异过大)"
        initial_status = '待定基准' if max_score < 0.1 else '待审批'

        db.session.add(CncOrder(
            order_no=final_order_no,
            manufacturer=data.get('manufacturer', '未知'),
            series=data.get('series', '-'),
            motor_count=int(data.get('motor_count', 0) or 0),
            drive_count=int(data.get('drive_count', 0) or 0),
            applicable_machine=data.get('applicable_machine', '-'),
            applicant=data.get('applicant', '未知'),
            status=initial_status,
            components_json=json.dumps(input_comps),
            matched_template=matched_name,
            match_score=max_score,
            match_adds=json.dumps(best_adds),
            match_rms=json.dumps(best_rms)
        ))
        db.session.commit()
        return jsonify({"status": "success", "order_no": final_order_no})

    if request.method == 'DELETE':
        order = db.session.get(CncOrder, request.args.get('id'))
        if order:
            db.session.delete(order)
            db.session.commit()
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "订单不存在"})


def init_database():
    db.create_all()
    if User.query.count() == 0:
        db.session.bulk_save_objects([
            User(username="admin", password="123456", role="超级管理员"),
            User(username="wang", password="1", role="标准管理员"),
            User(username="li", password="1", role="采购员"),
            User(username="chen", password="1", role="研发工程师")
        ])
    if Component.query.count() == 0:
        db.session.bulk_save_objects(
            [Component(category="电机", manufacturer="西门子", model="SIMOTICS-1FK7", remark="西门子伺服主轴电机", status="已发布"),
             Component(category="驱动", manufacturer="西门子", model="SINAMICS-S120", remark="多轴驱动模块底座", status="已发布")])
    if StandardTemplate.query.count() == 0:
        db.session.add(StandardTemplate(template_no="STA02026001", name="西门子840Dsl", motor_count=3,
                                        components_json=json.dumps(
                                            {"电机": ["SIMOTICS-1FK7"], "驱动": ["SINAMICS-S120"]})))
    db.session.commit()


if __name__ == '__main__':
    with app.app_context(): init_database()
    app.run(debug=True, host='0.0.0.0', port=6677)