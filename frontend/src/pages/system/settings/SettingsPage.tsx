import {
  Alert,
  App,
  Button,
  Card,
  Col,
  Form,
  Input,
  InputNumber,
  Row,
  Spin,
} from "antd";
import { useEffect, useState } from "react";

import { getErrorMessage } from "../../../api/errors";
import { useSettings, useUpdateSettings } from "../../../api/settings";
import { PageHeader } from "../../../components/PageHeader";
import {
  diffSettings,
  hasChanges,
  parseSettingsErrors,
  settingsToForm,
  type SettingsFormValues,
} from "./settingsForm";

const DESCRIPTION =
  "文档中所有“管理员可调”的平台参数集中在这里，只有系统管理员可见；修改写入审计日志。";

/** 接口里比例是 0.1–0.9，界面按原型显示成百分数。 */
const ratioFormatter = (value: number | undefined) =>
  value == null ? "" : `${Math.round(value * 100)}%`;

const ratioParser = (display: string | undefined) => {
  const text = (display ?? "").replace(/%/g, "").trim();
  return text === "" ? 0 : Number(text) / 100;
};

/** 422 返回的字段错误，按“分组.字段”取。 */
function fieldErrorProps(errors: Record<string, string>, path: string) {
  const text = errors[path];
  return text ? { validateStatus: "error" as const, help: text } : {};
}

export default function SettingsPage() {
  const { message } = App.useApp();
  const { data: settings, isLoading, error: loadError } = useSettings();
  const updateSettings = useUpdateSettings();
  const [form] = Form.useForm<SettingsFormValues>();
  const [values, setValues] = useState<SettingsFormValues | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [generalError, setGeneralError] = useState<string | null>(null);

  useEffect(() => {
    if (!settings) return;
    const initial = settingsToForm(settings);
    form.setFieldsValue(initial);
    setValues(initial);
  }, [settings, form]);

  const dirty =
    settings && values ? hasChanges(diffSettings(settings, values)) : false;

  // 有未保存修改时，刷新或关闭页面弹浏览器确认。
  useEffect(() => {
    if (!dirty) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const handleSave = () => {
    if (!settings || !values) return;
    const changes = diffSettings(settings, values);
    if (!hasChanges(changes)) {
      message.info("没有需要保存的修改");
      return;
    }
    setGeneralError(null);
    setFieldErrors({});
    updateSettings.mutate(changes, {
      onSuccess: () => message.success("已保存"),
      onError: (err) => {
        const errors = parseSettingsErrors(err);
        if (Object.keys(errors).length > 0) {
          setFieldErrors(errors);
        } else {
          setGeneralError(getErrorMessage(err));
        }
      },
    });
  };

  if (isLoading) {
    return (
      <>
        <PageHeader title="系统设置" description={DESCRIPTION} />
        <div style={{ display: "flex", justifyContent: "center", paddingTop: 80 }}>
          <Spin size="large" />
        </div>
      </>
    );
  }

  if (!settings) {
    return (
      <>
        <PageHeader title="系统设置" description={DESCRIPTION} />
        <Alert type="error" showIcon message={getErrorMessage(loadError)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="系统设置"
        description={DESCRIPTION}
        extra={
          <Button
            type="primary"
            onClick={handleSave}
            loading={updateSettings.isPending}
          >
            保存
          </Button>
        }
      />

      {generalError ? (
        <Alert
          type="error"
          showIcon
          message={generalError}
          style={{ marginBottom: 16 }}
        />
      ) : null}

      <Form
        form={form}
        layout="vertical"
        onValuesChange={(_changed, allValues) => {
          setValues(allValues);
          setFieldErrors({});
        }}
      >
        <Card title="密码与登录" style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                name={["password", "min_length"]}
                label="密码最小长度"
                rules={[{ required: true, message: "请输入密码最小长度" }]}
                {...fieldErrorProps(fieldErrors, "password.min_length")}
              >
                <InputNumber min={8} max={64} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name={["password", "min_classes"]}
                label="至少包含的字符类别"
                extra="大写、小写、数字、符号四类中"
                rules={[{ required: true, message: "请输入字符类别数" }]}
                {...fieldErrorProps(fieldErrors, "password.min_classes")}
              >
                <InputNumber min={1} max={4} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name={["login", "lock_threshold"]}
                label="连续失败锁定次数"
                extra="按“用户名 + 来源 IP”计数"
                rules={[{ required: true, message: "请输入锁定次数" }]}
                {...fieldErrorProps(fieldErrors, "login.lock_threshold")}
              >
                <InputNumber min={3} max={20} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name={["login", "lock_minutes"]}
                label="锁定时长（分钟）"
                rules={[{ required: true, message: "请输入锁定时长" }]}
                {...fieldErrorProps(fieldErrors, "login.lock_minutes")}
              >
                <InputNumber min={1} max={1440} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
        </Card>

        <Card title="会话" style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                name={["session", "access_minutes"]}
                label="访问令牌有效期（分钟）"
                rules={[{ required: true, message: "请输入访问令牌有效期" }]}
                {...fieldErrorProps(fieldErrors, "session.access_minutes")}
              >
                <InputNumber min={5} max={60} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name={["session", "refresh_days"]}
                label="刷新令牌有效期（天）"
                rules={[{ required: true, message: "请输入刷新令牌有效期" }]}
                {...fieldErrorProps(fieldErrors, "session.refresh_days")}
              >
                <InputNumber min={1} max={30} style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
        </Card>

        <Card title="模型调用">
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                name={["model_call", "ingest_rate_ratio"]}
                label="入库占用模型限额的比例"
                extra="模型设了限流时才起作用；入库和在线合计不超过模型限额"
                rules={[{ required: true, message: "请输入比例" }]}
                {...fieldErrorProps(fieldErrors, "model_call.ingest_rate_ratio")}
              >
                <InputNumber<number>
                  min={0.1}
                  max={0.9}
                  step={0.1}
                  style={{ width: "100%" }}
                  formatter={ratioFormatter}
                  parser={ratioParser}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name={["model_call", "model_host_allowlist"]}
                label="模型接口地址白名单"
                extra="模型接口地址默认不允许指向内网、本机；这里列出的地址和网段除外"
                {...fieldErrorProps(fieldErrors, "model_call.model_host_allowlist")}
              >
                <Input.TextArea rows={3} />
              </Form.Item>
            </Col>
          </Row>
        </Card>
      </Form>
    </>
  );
}
