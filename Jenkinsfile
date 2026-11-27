// Delivery pipeline (Guide §8.2). Runs after merge to main (SCM polling) and nightly (evaluation).
// Credentials: 'kubeconfig-kind' (secret file, exposed as CI_KUBECONFIG; scripts/lib/kube_guard.sh refuses any
// context that is not kind-idea-validator). Never commit kubeconfig. Jenkins itself: scripts/jenkins_up.sh.
pipeline {
  agent any
  options { timestamps(); disableConcurrentBuilds(); timeout(time: 60, unit: 'MINUTES') }
  triggers {
    pollSCM('H/2 * * * *')   // webhook cannot reach a laptop
    cron('H 2 * * *')        // nightly full evaluation
  }
  environment {
    TAG = "${env.GIT_COMMIT.take(7)}"
    REGISTRY = "localhost:5001"
    NS = "idea-validator"
  }
  stages {
    stage('Checkout') { steps { checkout scm } }
    stage('Build')    { steps { sh 'make images TAG=$TAG' } }
    stage('Push')     { steps { sh './scripts/push_images.sh $TAG' } }
    stage('Deploy') {
      steps {
        withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
          sh './scripts/deploy.sh $TAG'          // kustomize edit set image + apply + rollout status (timeout)
        }
      }
    }
    stage('Smoke') {
      steps { withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) { sh './scripts/smoke_test.sh' } }
    }
    stage('Eval') {
      when { triggeredBy 'TimerTrigger' }
      steps {
        withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
          sh 'kubectl apply -f k8s/base/eval/eval-job.yaml -n $NS && ./scripts/check_eval.sh'   // compare with stored baseline
        }
      }
    }
  }
  post {
    failure {
      echo 'Pipeline failed, nothing promoted'
      withCredentials([file(credentialsId: 'kubeconfig-kind', variable: 'CI_KUBECONFIG')]) {
        sh './scripts/rollback_changed.sh $TAG || true'   // kubectl rollout undo for deployments changed in this build
      }
    }
  }
}
