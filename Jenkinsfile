// gyj-api-pytest:接口自动化冒烟/回归(Windows 节点)
pipeline {
    agent any
    parameters {
        choice(name: 'SUITE', choices: ['smoke', 'regression'], description: '执行套件标记')
    }
    stages {
        stage('安装依赖') {
            steps {
                bat '.venv\\Scripts\\python -m pip install -r requirements.txt'
            }
        }
        stage('执行测试') {
            steps {
                bat ".venv\\Scripts\\python -m pytest -m %SUITE%"
            }
        }
    }
    post {
        always {
            allure results: [[path: 'reports/allure-results']]
        }
    }
}
